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

#: Characters Read the Docs preserves verbatim in a slug.
_SLUG_KEEP = re.compile(r"[^a-z0-9._-]+")


class NamingError(ValueError):
    """Raised when a value cannot yield a usable Read the Docs name."""


def slugify(value: str) -> str:
    """Convert a branch or project name into a Read the Docs slug.

    Read the Docs lowercases the value and replaces every character
    outside ``[a-z0-9._-]`` with a hyphen, so a branch named
    ``maintenance/3.7.10`` becomes ``maintenance-3.7.10``.

    Passing an unslugified name to the API produces a request path that
    does not resolve, so route every branch name through this function.
    """
    if not value or not value.strip():
        msg = "Cannot build a slug from an empty value"
        raise NamingError(msg)

    slug = _SLUG_KEEP.sub("-", value.strip().lower()).strip("-")
    if not slug:
        msg = f"Value {value!r} does not yield a usable slug"
        raise NamingError(msg)
    return slug


def umbrella_from_url(change_url: str) -> str:
    """Extract the umbrella name from a Gerrit change URL.

    Linux Foundation Gerrit hosts follow ``<host>.<umbrella>.org``, so
    ``https://gerrit.onap.org/r/c/cps/+/1`` yields ``onap`` and
    ``https://gerrit.o-ran-sc.org/r/...`` yields ``o-ran-sc``.

    Raises:
        NamingError: If the URL carries no recognisable umbrella.
    """
    if not change_url.strip():
        msg = "Cannot derive an umbrella from an empty change URL"
        raise NamingError(msg)

    host = urlparse(change_url.strip()).hostname or ""
    if not host:
        # Tolerate a bare host or a value with no scheme.
        host = change_url.strip().split("/")[0]

    labels = [label for label in host.split(".") if label]
    if len(labels) < 2:
        msg = f"Cannot derive an umbrella from {change_url!r}"
        raise NamingError(msg)

    # Drop the leading service label (gerrit, git) and the trailing
    # public suffix, leaving the organisation label.
    return slugify(labels[1])


def project_slug(umbrella: str, gerrit_project: str) -> str:
    """Build the Read the Docs project slug for a Gerrit project.

    A Gerrit project path may contain slashes, as in
    ``cps/ncmp-dmi-plugin``; Read the Docs joins the whole name with
    hyphens and prefixes the umbrella.
    """
    if not gerrit_project.strip():
        msg = "Cannot build a project slug from an empty Gerrit project"
        raise NamingError(msg)
    return slugify(f"{umbrella}-{gerrit_project}")


def parent_slug(umbrella: str, suffix: str) -> str:
    """Build the slug of the umbrella documentation project."""
    if not suffix.strip():
        return slugify(umbrella)
    return slugify(f"{umbrella}-{suffix}")


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
        overrides[slugify(source)] = slugify(target)
    return overrides


def apply_overrides(name: str, overrides: dict[str, str]) -> str:
    """Rewrite a derived name when an override names it."""
    return overrides.get(name, name)
