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
import shutil
import subprocess
from dataclasses import dataclass
from typing import Protocol, cast, final

log = logging.getLogger(__name__)

#: Exit status the CLI returns when the API reports a missing resource.
_FAILURE = 1


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

    def build_trigger(self, project: str, version: str) -> str:
        """Trigger a build and return its identifier."""
        ...

    def build_details(self, project: str, build_id: str) -> dict[str, object]:
        """Retrieve a build's details."""
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

    def _run(self, args: list[str]) -> object:
        """Run one command and return its decoded payload."""
        command = [*self.config.command(), "rtd", *args, "--json"]
        log.debug("Running: %s", " ".join(command))

        try:
            completed = subprocess.run(  # noqa: S603 - argv built from typed inputs
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

        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            if "not found" in detail.lower():
                raise NotFoundError(detail)
            msg = f"Read the Docs command failed: {detail or 'no output'}"
            raise ReadTheDocsError(msg)

        if not completed.stdout.strip():
            return {}

        try:
            return cast("object", json.loads(completed.stdout))
        except json.JSONDecodeError as exc:
            msg = f"Read the Docs command returned unreadable output: {completed.stdout[:200]}"
            raise ReadTheDocsError(msg) from exc

    def _run_object(self, args: list[str]) -> dict[str, object]:
        """Run a command expected to return a JSON object."""
        payload = self._run(args)
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

    def build_trigger(self, project: str, version: str) -> str:
        """Trigger a build and return its identifier."""
        payload = self._run_object(["project-build-trigger", project, version])
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
