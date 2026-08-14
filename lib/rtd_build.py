# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Run the Read the Docs verify and merge lanes.

The verify lane reports what a merge would do without changing anything.
The merge lane creates the project when absent, attaches it to the
umbrella project, sets the landing version, triggers a build and waits
for the result.

Name resolution runs automatically. A caller supplies the Gerrit project
and change URL, and the module derives the Read the Docs slugs. Explicit
inputs override any derived value.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from lib.rtd_client import (
    ReadTheDocsError,
    SupportsReadTheDocs,
)
from lib.rtd_naming import (
    apply_overrides,
    parent_slug,
    parse_overrides,
    project_slug,
    repository_url_from,
    slugify,
    umbrella_from,
)

log = logging.getLogger(__name__)

MODE_VERIFY = "verify"
MODE_MERGE = "merge"

#: Build states Read the Docs reports as a completed failure.
_FAILED_STATES = {"failed", "cancelled"}

#: Branch names Read the Docs publishes under its ``latest`` alias.
#: Read the Docs never creates a version slug for the default branch; it
#: tracks that branch as ``latest`` instead, so asking it to build a
#: version named ``master`` addresses a version that does not exist.
DEFAULT_BRANCH_NAMES = ("master", "main")

#: The slug under which Read the Docs publishes the default branch.
#:
#: Read the Docs fixes this name, so it stays a constant rather than an
#: input. It differs from ``default_version``, which names whichever
#: version the landing page serves: a project may point its landing page
#: at a release branch while its default branch still builds as
#: ``latest``. Conflating the two builds the wrong version.
LATEST_ALIAS = "latest"


@dataclass(frozen=True)
class Settings:
    """Everything one run needs."""

    mode: str = MODE_VERIFY
    gerrit_project: str = ""
    gerrit_url: str = ""
    gerrit_change_url: str = ""
    branch: str = ""
    default_branch: str = ""
    project: str = ""
    parent_project: str = ""
    parent_suffix: str = "doc"
    project_overrides: str = ""
    default_version: str = "latest"
    repository_url: str = ""
    homepage: str = ""
    build_timeout: int = 1800
    poll_interval: int = 10
    create_timeout: int = 600


@dataclass
class Outcome:
    """What the run determined and changed."""

    mode: str = MODE_VERIFY
    project: str = ""
    parent_project: str = ""
    version_slug: str = ""
    project_exists: bool = False
    project_created: bool = False
    subproject_created: bool = False
    default_version_changed: bool = False
    version_activated: bool = False
    build_id: str = ""
    build_success: bool = False
    documentation_url: str = ""
    notes: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        """Record a human-readable note and log it."""
        self.notes.append(message)
        log.info(message)

    def as_dict(self) -> dict[str, object]:
        """Render the outcome for JSON output."""
        return {
            "mode": self.mode,
            "project": self.project,
            "parent_project": self.parent_project,
            "version_slug": self.version_slug,
            "project_exists": self.project_exists,
            "project_created": self.project_created,
            "subproject_created": self.subproject_created,
            "default_version_changed": self.default_version_changed,
            "version_activated": self.version_activated,
            "build_id": self.build_id,
            "build_success": self.build_success,
            "documentation_url": self.documentation_url,
            "notes": self.notes,
        }


def resolve_names(
    settings: Settings,
    client: SupportsReadTheDocs,
    outcome: Outcome,
) -> tuple[str, str]:
    """Determine the project and parent project slugs.

    Explicit inputs win. Otherwise both derive from the Gerrit project and
    change URL. Where a derived umbrella documentation project is absent
    but the bare umbrella project exists, the bare name wins: an
    organisation that imported existing documentation holds its top-level
    docs under the umbrella name itself.
    """
    overrides = parse_overrides(settings.project_overrides)

    if settings.project and settings.parent_project:
        return (
            apply_overrides(slugify(settings.project), overrides),
            apply_overrides(slugify(settings.parent_project), overrides),
        )

    umbrella = umbrella_from(settings.gerrit_url, settings.gerrit_change_url)

    parent = settings.parent_project or parent_slug(umbrella, settings.parent_suffix)
    parent = apply_overrides(slugify(parent), overrides)
    if not settings.parent_project and not client.project_exists(parent):
        bare = slugify(umbrella)
        if client.project_exists(bare):
            outcome.note(
                f"Umbrella project {parent!r} is absent; using {bare!r} instead"
            )
            parent = bare

    project = settings.project or project_slug(umbrella, settings.gerrit_project)
    project = apply_overrides(slugify(project), overrides)
    if (
        not settings.project
        and project != parent
        and not client.project_exists(project)
    ):
        # The umbrella's own documentation repository resolves to the
        # umbrella project rather than a separate one.
        if slugify(settings.gerrit_project) == slugify(settings.parent_suffix):
            bare = slugify(umbrella)
            if client.project_exists(bare):
                outcome.note(f"Project {project!r} is absent; using {bare!r} instead")
                project = bare

    return project, parent


def wait_for_build(
    client: SupportsReadTheDocs,
    project: str,
    version: str,
    settings: Settings,
    outcome: Outcome,
) -> None:
    """Trigger a build and wait for Read the Docs to finish it."""
    build_id = client.build_trigger(project, version)
    outcome.build_id = build_id
    outcome.note(f"Triggered build {build_id} of {project}/{version}")

    deadline = time.monotonic() + settings.build_timeout
    while time.monotonic() < deadline:
        time.sleep(settings.poll_interval)
        details = client.build_details(project, build_id)
        success = details.get("success")
        state = str(details.get("state", "")).lower()

        if success is True:
            outcome.build_success = True
            outcome.note(f"Build {build_id} completed successfully")
            return
        if success is False or state in _FAILED_STATES:
            msg = f"Read the Docs build {build_id} of {project}/{version} failed"
            raise ReadTheDocsError(msg)
        log.info("Build %s in state %s; waiting", build_id, state or "unknown")

    msg = f"Read the Docs build {build_id} did not finish within {settings.build_timeout}s"
    raise ReadTheDocsError(msg)


def ensure_project(
    client: SupportsReadTheDocs, settings: Settings, outcome: Outcome
) -> None:
    """Create the project when Read the Docs does not yet hold it."""
    if client.project_exists(outcome.project):
        outcome.project_exists = True
        outcome.note(f"Project {outcome.project!r} exists")
        return

    homepage = settings.homepage or f"https://{outcome.project}.readthedocs.io"

    # A change URL points at a review rather than a repository, so
    # recording it would leave the new project unable to clone anything.
    repository = settings.repository_url or repository_url_from(
        settings.gerrit_url,
        settings.gerrit_change_url,
        settings.gerrit_project,
    )

    outcome.note(f"Creating project {outcome.project!r} from {repository}")
    _ = client.project_create(outcome.project, repository, homepage)
    outcome.project_created = True

    deadline = time.monotonic() + settings.create_timeout
    while time.monotonic() < deadline:
        time.sleep(settings.poll_interval)
        if client.project_exists(outcome.project):
            outcome.project_exists = True
            outcome.note(f"Project {outcome.project!r} is now visible")
            return

    msg = (
        f"Project {outcome.project!r} did not appear within {settings.create_timeout}s"
    )
    raise ReadTheDocsError(msg)


def ensure_subproject(client: SupportsReadTheDocs, outcome: Outcome) -> None:
    """Attach the project to its umbrella when it stands apart from it."""
    if outcome.project == outcome.parent_project:
        outcome.note("Project is its own umbrella; no subproject relationship needed")
        return

    if outcome.project in client.subproject_list(outcome.parent_project):
        outcome.note(
            f"Subproject relationship {outcome.parent_project}/{outcome.project} exists"
        )
        return

    outcome.note(
        f"Creating subproject relationship {outcome.parent_project}/{outcome.project}"
    )
    _ = client.subproject_create(outcome.parent_project, outcome.project)
    outcome.subproject_created = True


def ensure_default_version(
    client: SupportsReadTheDocs, settings: Settings, outcome: Outcome
) -> None:
    """Point the project's landing page at the configured version."""
    if not settings.default_version:
        return

    current = client.default_version(outcome.project)
    if current == settings.default_version:
        outcome.note(f"Landing version already {current!r}")
        return

    outcome.note(
        f"Setting landing version from {current!r} to {settings.default_version!r}"
    )
    _ = client.project_update(outcome.project, default_version=settings.default_version)
    outcome.default_version_changed = True


def tracks_latest(branch: str, settings: Settings) -> bool:
    """Report whether a branch publishes under the default version.

    Read the Docs tracks the repository's default branch as ``latest``
    rather than giving it a version slug of its own. An explicit
    ``default_branch`` names that branch; otherwise the usual defaults
    apply, neither of which collides with a real version slug.
    """
    if not branch:
        return True
    candidate = slugify(branch)
    if settings.default_branch:
        return candidate == slugify(settings.default_branch)
    return candidate in DEFAULT_BRANCH_NAMES


def build_branch(
    client: SupportsReadTheDocs, settings: Settings, outcome: Outcome
) -> None:
    """Build the branch under change, discovering it when necessary."""
    version = outcome.version_slug

    if version == LATEST_ALIAS:
        wait_for_build(client, outcome.project, version, settings, outcome)
        return

    if client.version_active(outcome.project, version) is None:
        discovery = f"building {LATEST_ALIAS!r} to trigger branch discovery"
        outcome.note(f"Read the Docs has not seen {version!r}; {discovery}")
        wait_for_build(client, outcome.project, LATEST_ALIAS, settings, outcome)

    wait_for_build(client, outcome.project, version, settings, outcome)

    if client.version_active(outcome.project, version) is False:
        outcome.note(f"Marking {version!r} active")
        _ = client.version_activate(outcome.project, version)
        outcome.version_activated = True


def run_verify(client: SupportsReadTheDocs, outcome: Outcome) -> None:
    """Report what a merge would do, changing nothing."""
    outcome.project_exists = client.project_exists(outcome.project)
    if outcome.project_exists:
        outcome.note(f"Project {outcome.project!r} exists")
    else:
        outcome.note(f"Project {outcome.project!r} is absent; a merge will create it")


def run_merge(
    client: SupportsReadTheDocs, settings: Settings, outcome: Outcome
) -> None:
    """Publish the documentation for a merged change."""
    ensure_project(client, settings, outcome)
    ensure_subproject(client, outcome)
    ensure_default_version(client, settings, outcome)
    build_branch(client, settings, outcome)


def run(settings: Settings, client: SupportsReadTheDocs) -> Outcome:
    """Resolve names and run the requested lane."""
    outcome = Outcome(mode=settings.mode)
    project, parent = resolve_names(settings, client, outcome)
    outcome.project = project
    outcome.parent_project = parent

    if tracks_latest(settings.branch, settings):
        outcome.version_slug = LATEST_ALIAS
        if settings.branch:
            tracked = f"{LATEST_ALIAS!r}, which tracks the default branch"
            outcome.note(f"Branch {settings.branch!r} publishes as {tracked}")
    else:
        outcome.version_slug = slugify(settings.branch)

    outcome.documentation_url = f"https://{project}.readthedocs.io"

    outcome.note(f"Read the Docs project: {outcome.documentation_url}")
    outcome.note(f"Umbrella project: https://{parent}.readthedocs.io")
    outcome.note(f"Version slug: {outcome.version_slug}")

    if settings.mode == MODE_MERGE:
        run_merge(client, settings, outcome)
    else:
        run_verify(client, outcome)

    return outcome
