# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Drive the Read the Docs API through the lftools-uv command line.

Every call asks for ``--json`` and parses the result, which the refactored
``rtd`` command group emits consistently. Diagnostics travel on stderr, so
a warning from a dependency cannot corrupt the payload this module reads.

Running the tool through ``uvx`` keeps the release-engineering
dependencies out of the interpreter a documentation build uses. That
separation matters here: an earlier pipeline installed the tooling
alongside the project's own requirements, and a transitive dependency
that dropped support for the project's Python version broke every merge.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Protocol, cast, final

log = logging.getLogger(__name__)

#: Exit status the CLI returns when the API reports a missing resource.
_FAILURE = 1

#: Attempts a command gets when Read the Docs fails it transiently.
ATTEMPTS = 5

#: Longest pause, in seconds, before repeating a command. Read the Docs
#: names its own wait when it throttles; a cap keeps an outsized hint
#: from stalling the job.
MAX_WAIT = 60

_THROTTLED = re.compile(r"\bHTTP 429\b")
_THROTTLE_HINT = re.compile(r"Expected available in (\d+) seconds?")
_SERVER_ERROR = re.compile(r"\bHTTP 5\d\d\b")

#: Commands that leave the same result however often they run: reads,
#: and updates that set a fixed value. Only these repeat after a server
#: error, since the failed attempt may already have taken effect.
#: Creating a project or subproject, or triggering a build, does not
#: qualify.
_REPEATABLE = frozenset(
    {
        "project-details",
        "project-update",
        "project-version-details",
        "project-version-update",
        "project-build-details",
        "project-build-list",
        "subproject-list",
    }
)


def retry_wait(command: str, detail: str, attempt: int) -> int | None:
    """Return the seconds to wait before repeating a failed command.

    None means the failure is not transient, or the command cannot
    safely repeat after it. A throttled request never reached the API's
    handler, so any command may repeat once the named wait passes.
    """
    if _THROTTLED.search(detail):
        hint = _THROTTLE_HINT.search(detail)
        # One second beyond the hint, as Read the Docs rounds it down.
        wait = int(hint.group(1)) + 1 if hint else 1 << attempt
        return min(wait, MAX_WAIT)
    if _SERVER_ERROR.search(detail) and command in _REPEATABLE:
        return min(1 << attempt, MAX_WAIT)
    return None


class ReadTheDocsError(RuntimeError):
    """Raised when a Read the Docs command fails."""


class NotFoundError(ReadTheDocsError):
    """Raised when the API reports a missing project, version or build."""


@dataclass(frozen=True)
class ClientConfig:
    """How to invoke the command line."""

    package: str = "lftools-uv"
    version: str = ""
    launcher: str = "uvx"

    def command(self) -> list[str]:
        """Build the command prefix that runs the tool."""
        if self.version:
            spec = f"{self.package}=={self.version}"
        else:
            spec = self.package
        return [self.launcher, "--quiet", "--from", spec, self.package]


class SupportsReadTheDocs(Protocol):
    """The API surface the lanes depend on.

    Declaring the surface as a protocol lets the tests supply an
    in-memory double without inheriting from the subprocess-backed
    client, and keeps the lanes independent of how the calls travel.
    """

    def project_exists(self, project: str) -> bool:
        """Report whether a project exists."""
        ...

    def project_create(
        self, name: str, repository_url: str, homepage: str
    ) -> dict[str, object]:
        """Create a project."""
        ...

    def project_update(self, project: str, **fields: str) -> dict[str, object]:
        """Update project fields."""
        ...

    def default_version(self, project: str) -> str:
        """Return the project's landing version."""
        ...

    def version_active(self, project: str, version: str) -> bool | None:
        """Report whether a version is active, or None when unknown."""
        ...

    def version_activate(self, project: str, version: str) -> dict[str, object]:
        """Mark a version active."""
        ...

    def build_trigger(
        self, project: str, version: str, deadline: float | None = None
    ) -> str:
        """Trigger a build and return its identifier.

        A deadline, on the time.monotonic clock, stops retries that
        would start the build after the caller has stopped waiting.
        """
        ...

    def build_details(self, project: str, build_id: str) -> dict[str, object]:
        """Retrieve a build's details."""
        ...

    def build_list(self, project: str) -> list[dict[str, object]]:
        """List the builds Read the Docs has not yet finished."""
        ...

    def subproject_list(self, project: str) -> list[str]:
        """List the subproject slugs attached to a project."""
        ...

    def subproject_create(self, project: str, subproject: str) -> dict[str, object]:
        """Attach a project to a parent as a subproject."""
        ...


@final
class ReadTheDocsClient:
    """A thin, typed wrapper over the ``lftools-uv rtd`` commands."""

    def __init__(self, config: ClientConfig | None = None, timeout: int = 300) -> None:
        self.config: ClientConfig = config or ClientConfig()
        self.timeout: int = timeout

    def _invoke(
        self, command: list[str], args: list[str]
    ) -> subprocess.CompletedProcess[str]:
        """Run the command line once."""
        try:
            return subprocess.run(  # noqa: S603 - argv built from typed inputs
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            msg = f"Read the Docs command timed out after {self.timeout}s: {' '.join(args)}"
            raise ReadTheDocsError(msg) from exc
        except FileNotFoundError as exc:
            msg = f"Cannot run {self.config.launcher!r}; install uv or set the launcher input"
            raise ReadTheDocsError(msg) from exc

    def _run(self, args: list[str], deadline: float | None = None) -> object:
        """Run one command and return its decoded payload.

        Throttling and server errors are transient, so the command
        repeats where retry_wait allows, up to ATTEMPTS times and never
        past the deadline, when the caller sets one.
        """
        command = [*self.config.command(), "rtd", *args, "--json"]
        log.debug("Running: %s", " ".join(command))

        attempt = 1
        completed = self._invoke(command, args)
        while completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            if "not found" in detail.lower():
                raise NotFoundError(detail)
            wait = retry_wait(args[0], detail, attempt)
            # A retry that would land past the caller's deadline is
            # pointless, and for a trigger harmful: the build would
            # start after the caller has stopped watching for it.
            late = deadline is not None and time.monotonic() + (wait or 0) >= deadline
            if wait is None or attempt >= ATTEMPTS or late:
                msg = f"Read the Docs command failed: {detail or 'no output'}"
                raise ReadTheDocsError(msg)
            log.warning(
                "Read the Docs %s failed transiently (attempt %d of %d); retrying in %ds",
                args[0],
                attempt,
                ATTEMPTS,
                wait,
            )
            time.sleep(wait)
            attempt += 1
            completed = self._invoke(command, args)

        if not completed.stdout.strip():
            return {}

        try:
            return cast("object", json.loads(completed.stdout))
        except json.JSONDecodeError as exc:
            msg = f"Read the Docs command returned unreadable output: {completed.stdout[:200]}"
            raise ReadTheDocsError(msg) from exc

    def _run_object(
        self, args: list[str], deadline: float | None = None
    ) -> dict[str, object]:
        """Run a command expected to return a JSON object."""
        payload = self._run(args, deadline)
        if not isinstance(payload, dict):
            msg = f"Expected an object from {' '.join(args)}"
            raise ReadTheDocsError(msg)
        return cast("dict[str, object]", payload)

    # -- Projects ------------------------------------------------------------

    def project_details(self, project: str) -> dict[str, object]:
        """Retrieve a project's details."""
        return self._run_object(["project-details", project])

    def project_exists(self, project: str) -> bool:
        """Report whether a project exists."""
        try:
            _ = self.project_details(project)
        except NotFoundError:
            return False
        return True

    def project_create(
        self,
        name: str,
        repository_url: str,
        homepage: str,
        repository_type: str = "git",
        programming_language: str = "py",
        language: str = "en",
    ) -> dict[str, object]:
        """Create a project."""
        return self._run_object(
            [
                "project-create",
                name,
                repository_url,
                repository_type,
                homepage,
                programming_language,
                language,
            ]
        )

    def project_update(self, project: str, **fields: str) -> dict[str, object]:
        """Update project fields expressed as ``key=value`` pairs."""
        pairs = [f"{key}={value}" for key, value in fields.items()]
        return self._run_object(["project-update", project, *pairs])

    def default_version(self, project: str) -> str:
        """Return the project's landing version."""
        details = self.project_details(project)
        value = details.get("default_version")
        return "" if value is None else str(value)

    # -- Versions ------------------------------------------------------------

    def version_details(self, project: str, version: str) -> dict[str, object] | None:
        """Return a version's details, or None when Read the Docs has none."""
        try:
            return self._run_object(["project-version-details", project, version])
        except NotFoundError:
            return None

    def version_active(self, project: str, version: str) -> bool | None:
        """Report whether a version is active, or None when it is unknown."""
        details = self.version_details(project, version)
        if details is None:
            return None
        return details.get("active") is True

    def version_activate(self, project: str, version: str) -> dict[str, object]:
        """Mark a version active so it appears in the version picker."""
        return self._run_object(["project-version-update", project, version, "true"])

    # -- Builds --------------------------------------------------------------

    def build_trigger(
        self, project: str, version: str, deadline: float | None = None
    ) -> str:
        """Trigger a build and return its identifier."""
        payload = self._run_object(
            ["project-build-trigger", project, version], deadline
        )
        build = payload.get("build")
        if isinstance(build, dict):
            identifier = cast("dict[str, object]", build).get("id")
            if identifier is not None:
                return str(identifier)
        identifier = payload.get("id")
        if identifier is not None:
            return str(identifier)
        msg = f"Read the Docs returned no build id for {project}/{version}"
        raise ReadTheDocsError(msg)

    def build_details(self, project: str, build_id: str) -> dict[str, object]:
        """Retrieve a build's details."""
        return self._run_object(["project-build-details", project, build_id])

    def build_list(self, project: str) -> list[dict[str, object]]:
        """List the builds Read the Docs has not yet finished.

        The command asks the API for running builds only, which includes
        queued ones: everything short of a final state.
        """
        payload = self._run_object(["project-build-list", project])
        entries = payload.get("builds")
        if not isinstance(entries, list):
            return []
        return [
            cast("dict[str, object]", entry)
            for entry in cast("list[object]", entries)
            if isinstance(entry, dict)
        ]

    # -- Subprojects ---------------------------------------------------------

    def subproject_list(self, project: str) -> list[str]:
        """List the subproject slugs attached to a project."""
        payload = self._run_object(["subproject-list", project])
        entries = payload.get("subprojects")
        if not isinstance(entries, list):
            return []
        return [str(entry) for entry in cast("list[object]", entries)]

    def subproject_create(self, project: str, subproject: str) -> dict[str, object]:
        """Attach a project to a parent as a subproject."""
        return self._run_object(["subproject-create", project, subproject])


def launcher_available(launcher: str) -> bool:
    """Report whether the launcher exists on PATH."""
    return shutil.which(launcher) is not None
