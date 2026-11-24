# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Check that the requested lftools-uv release is one the action supports."""

from __future__ import annotations

import re

from lib.rtd_client import ReadTheDocsError

#: Oldest lftools-uv release the action runs: 0.11.0 is the first with
#: the ``pinned`` extra, and it exits 3 for a missing resource.
MINIMUM_VERSION = (0, 11, 0)

#: The release number, after the optional ``v`` prefix and ``N!`` epoch
#: that PEP 440 allows.
_RELEASE = re.compile(r"v?(?:(?P<epoch>\d+)!)?(?P<release>\d+(?:\.\d+)*)", re.I)

#: A pre-release or development segment straight after the release
#: number, spelt as PEP 440 allows; it sorts before the final release.
_BEFORE_FINAL = re.compile(r"[-_.]?(?:a|b|c|rc|alpha|beta|pre|preview|dev)", re.I)


def require_supported_version(version: str) -> None:
    """Refuse an lftools-uv version older than MINIMUM_VERSION.

    An empty version runs the newest release, which qualifies. The
    release numbers are compared, not the strings, so 0.9.0 and 0.10.1
    are older than 0.11.0 and 0.100.0 is not. A pre-release or
    development release of 0.11.0, such as 0.11.0rc1 or 0.11.0.dev1,
    comes before 0.11.0 and is refused too. A leading ``v`` and an
    explicit ``0!`` epoch change nothing; a later epoch sorts after
    every release without one, so it qualifies. Surrounding whitespace
    is ignored, as PEP 440 says.
    """
    version = version.strip()
    if not version:
        return
    release = _RELEASE.match(version)
    if release is None:
        msg = f"lftools_version {version!r} is not a release number"
        raise ReadTheDocsError(msg)
    if int(release.group("epoch") or 0) > 0:
        return
    numbers = tuple(int(part) for part in release.group("release").split("."))
    width = max(len(numbers), len(MINIMUM_VERSION))
    padded = numbers + (0,) * (width - len(numbers))
    minimum = MINIMUM_VERSION + (0,) * (width - len(MINIMUM_VERSION))
    early = _BEFORE_FINAL.match(version, release.end()) is not None
    if padded < minimum or (padded == minimum and early):
        oldest = ".".join(str(part) for part in MINIMUM_VERSION)
        msg = (
            f"lftools_version {version} is older than {oldest}, the first "
            + "lftools-uv release with the pinned extra and the exit "
            + "status this action reads for a missing resource"
        )
        raise ReadTheDocsError(msg)
