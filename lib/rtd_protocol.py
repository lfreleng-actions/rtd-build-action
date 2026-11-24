# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""The Read the Docs API surface the lanes depend on."""

from __future__ import annotations

from typing import Protocol


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

    def build_details(
        self, project: str, build_id: str, deadline: float | None = None
    ) -> dict[str, object]:
        """Retrieve a build's details, retrying no later than the deadline."""
        ...

    def build_list(
        self, project: str, deadline: float | None = None
    ) -> list[dict[str, object]]:
        """List the builds Read the Docs has not yet finished."""
        ...

    def subproject_list(self, project: str) -> list[str]:
        """List the subproject slugs attached to a project."""
        ...

    def subproject_create(self, project: str, subproject: str) -> dict[str, object]:
        """Attach a project to a parent as a subproject."""
        ...
