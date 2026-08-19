# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Derive Read the Docs project and version names.

Read the Docs addresses a project and a version by slug. A Gerrit change
supplies a project path and a change URL instead, so these helpers turn
those into the names the API expects. Every derived value yields to an
explicit override, and the derivation runs by default so a caller needs
no configuration in the common case.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

#: Characters Read the Docs preserves verbatim in a version slug. The
#: dot survives because versions carry one: ``3.7.10`` must stay intact.
_SLUG_KEEP = re.compile(r"[^a-z0-9._-]+")

#: Characters Read the Docs preserves verbatim in a PROJECT slug, which
#: is a Django slug field and so admits no dot.
_PROJECT_KEEP = re.compile(r"[^a-z0-9_-]+")

#: Runs of hyphens left behind once a separator gets replaced.
_HYPHEN_RUN = re.compile(r"-{2,}")


class NamingError(ValueError):
    """Raised when a value cannot yield a usable Read the Docs name."""


def slugify(value: str) -> str:
    """Convert a branch name into a Read the Docs VERSION slug.

    Read the Docs lowercases the value and replaces every character
    outside ``[a-z0-9._-]`` with a hyphen, so a branch named
    ``maintenance/3.7.10`` becomes ``maintenance-3.7.10``. The dot
    survives because a version carries one.

    Passing an unslugified name to the API produces a request path that
    does not resolve, so route every branch name through this function.

    Project names follow different rules; see :func:`project_slugify`.
    """
    if not value or not value.strip():
        msg = "Cannot build a slug from an empty value"
        raise NamingError(msg)

    slug = _SLUG_KEEP.sub("-", value.strip().lower()).strip("-")
    if not slug:
        msg = f"Value {value!r} does not yield a usable slug"
        raise NamingError(msg)
    return slug


def project_slugify(value: str) -> str:
    """Convert a project name into a Read the Docs PROJECT slug.

    A project slug is a Django slug field, which admits letters, digits,
    underscores and hyphens. It admits no dot, so a Gerrit project such
    as ``.github`` cannot reuse :func:`slugify`: that function keeps the
    dot on purpose, for versions like ``3.7.10``, and the resulting
    ``onap-.github`` draws HTTP 400 from the API.

    Replacing a separator can leave a run of hyphens, as ``onap-.github``
    would, so collapse those and trim the ends.
    """
    if not value or not value.strip():
        msg = "Cannot build a project slug from an empty value"
        raise NamingError(msg)

    slug = _PROJECT_KEEP.sub("-", value.strip().lower())
    slug = _HYPHEN_RUN.sub("-", slug).strip("-")
    if not slug:
        msg = f"Value {value!r} does not yield a usable project slug"
        raise NamingError(msg)
    return slug


def umbrella_from_url(url: str) -> str:
    """Extract the umbrella name from a Gerrit URL.

    Accepts either the Gerrit server URL or a change URL, since both
    carry the same host. Linux Foundation Gerrit hosts follow
    ``<service>.<umbrella>.org``, so a host of ``gerrit.onap.org`` yields
    ``onap`` and ``gerrit.o-ran-sc.org`` yields ``o-ran-sc``, whatever
    path follows.

    Raises:
        NamingError: If the URL carries no recognisable umbrella.
    """
    if not url.strip():
        msg = "Cannot derive an umbrella from an empty URL"
        raise NamingError(msg)

    host = urlparse(url.strip()).hostname or ""
    if not host:
        # Tolerate a bare host or a value with no scheme.
        host = url.strip().split("/")[0]

    labels = [label for label in host.split(".") if label]
    if len(labels) < 2:
        msg = f"Cannot derive an umbrella from {url!r}"
        raise NamingError(msg)

    # Drop the leading service label (gerrit, git) and the trailing
    # public suffix, leaving the organisation label. The umbrella feeds
    # project slugs, so it follows the project rules.
    return project_slugify(labels[1])


def umbrella_from(gerrit_url: str, change_url: str) -> str:
    """Determine the umbrella from whichever Gerrit URL a caller supplied.

    Prefers the server URL, which names the Gerrit host directly. Falls
    back to a change URL, which carries the same host inside a review
    path.

    Raises:
        NamingError: If neither value yields an umbrella.
    """
    for candidate in (gerrit_url, change_url):
        if candidate.strip():
            return umbrella_from_url(candidate)

    msg = "Supply gerrit_url or gerrit_change_url so the umbrella can be derived"
    raise NamingError(msg)


def repository_url_from_server(gerrit_url: str, gerrit_project: str) -> str:
    """Build a repository URL from the Gerrit server URL and project path.

    The server URL already addresses the Gerrit instance's repository
    root, so the project path appends directly. This needs no knowledge
    of how a host lays out its review paths.

    Raises:
        NamingError: If either value is empty.
    """
    if not gerrit_url.strip():
        msg = "Cannot derive a repository URL from an empty Gerrit URL"
        raise NamingError(msg)
    if not gerrit_project.strip():
        msg = "Cannot derive a repository URL without a Gerrit project"
        raise NamingError(msg)

    base = gerrit_url.strip().rstrip("/")
    project = gerrit_project.strip().strip("/")
    return f"{base}/{project}"


def repository_url_from_change(change_url: str, gerrit_project: str) -> str:
    """Derive a repository URL from a Gerrit change URL.

    Prefer :func:`repository_url_from_server` where the Gerrit server URL
    is available. This function covers the case where a caller holds only
    a change URL, which points at a review rather than a repository:
    recording it against a Read the Docs project would leave that project
    unable to clone anything.

    Gerrit change URLs carry the review path before a ``/c/`` segment,
    and hosts differ in what that prefix is: ONAP serves reviews under
    ``/r/c/...`` and OpenDaylight under ``/gerrit/c/...``. Taking
    everything before ``/c/`` keeps both correct.

    Raises:
        NamingError: If the URL carries no recognisable review path.
    """
    if not gerrit_project.strip():
        msg = "Cannot derive a repository URL without a Gerrit project"
        raise NamingError(msg)

    parsed = urlparse(change_url.strip())
    if not parsed.scheme or not parsed.hostname:
        msg = f"Cannot derive a repository URL from {change_url!r}"
        raise NamingError(msg)

    marker = "/c/"
    path = parsed.path
    if marker not in path:
        msg = (
            f"Cannot derive a repository URL from {change_url!r}: "
            "the path carries no '/c/' review segment. Set gerrit_url or "
            "repository_url."
        )
        raise NamingError(msg)

    base = path.split(marker, 1)[0].rstrip("/")
    project = gerrit_project.strip().strip("/")
    return f"{parsed.scheme}://{parsed.netloc}{base}/{project}"


def repository_url_from(gerrit_url: str, change_url: str, gerrit_project: str) -> str:
    """Determine the repository URL from whichever inputs a caller supplied.

    Prefers the Gerrit server URL, since joining it with the project path
    needs no knowledge of a host's review layout.

    Raises:
        NamingError: If neither value yields a repository URL.
    """
    if gerrit_url.strip():
        return repository_url_from_server(gerrit_url, gerrit_project)
    if change_url.strip():
        return repository_url_from_change(change_url, gerrit_project)

    msg = "Supply gerrit_url, gerrit_change_url or repository_url to create a project"
    raise NamingError(msg)


def project_slug(umbrella: str, gerrit_project: str) -> str:
    """Build the Read the Docs project slug for a Gerrit project.

    A Gerrit project path may contain slashes, as in
    ``cps/ncmp-dmi-plugin``; Read the Docs joins the whole name with
    hyphens and prefixes the umbrella.
    """
    if not gerrit_project.strip():
        msg = "Cannot build a project slug from an empty Gerrit project"
        raise NamingError(msg)
    return project_slugify(f"{umbrella}-{gerrit_project}")


def parent_slug(umbrella: str, suffix: str) -> str:
    """Build the slug of the umbrella documentation project."""
    if not suffix.strip():
        return project_slugify(umbrella)
    return project_slugify(f"{umbrella}-{suffix}")


def parse_overrides(raw: str) -> dict[str, str]:
    """Parse newline or comma separated ``from=to`` rewrites.

    Provides an escape hatch for a project whose Read the Docs name
    follows no derivable rule.
    """
    overrides: dict[str, str] = {}
    for entry in re.split(r"[\n,]+", raw or ""):
        item = entry.strip()
        if not item:
            continue
        if "=" not in item:
            msg = f"Expected 'from=to' in project overrides, received {item!r}"
            raise NamingError(msg)
        source, target = item.split("=", 1)
        if not source.strip() or not target.strip():
            msg = f"Both sides of a project override must carry a value: {item!r}"
            raise NamingError(msg)
        overrides[project_slugify(source)] = project_slugify(target)
    return overrides


def apply_overrides(name: str, overrides: dict[str, str]) -> str:
    """Rewrite a derived name when an override names it."""
    return overrides.get(name, name)
