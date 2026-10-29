# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Trigger a Read the Docs build and wait for the branch to publish.

Read the Docs keeps one build queue per version and cancels a queued or
running build when another is triggered for the same version. A
cancellation therefore says nothing about the documentation, so this
module follows the build that superseded ours rather than reporting a
failure.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from lib.rtd_client import ReadTheDocsError, SupportsReadTheDocs

log = logging.getLogger(__name__)

#: Build states after which Read the Docs does no further work.
_FINAL_STATES = {"finished", "failed", "cancelled"}

#: The state of a build that another build of its version superseded.
_CANCELLED = "cancelled"

#: Builds the merge lane triggers before accepting that Read the Docs
#: keeps cancelling them. A cancellation with no newer build to follow
#: is rare, so a small bound suffices; an unbounded loop could hammer
#: the API for the whole build timeout.
#:
#: Following a newer build never counts against this bound. Each one
#: was triggered after ours and so contains the change, a burst of
#: merges can supersede several in a row, and following one only
#: polls: the build timeout bounds that wait however long the chain.
MAX_TRIGGERS = 3


@dataclass(frozen=True)
class Polling:
    """How long to wait for a build, and how often to look."""

    timeout: int = 1800
    interval: int = 10


@dataclass(frozen=True)
class Target:
    """The project version a build publishes."""

    project: str
    version: str


def build_state(details: dict[str, object]) -> str:
    """Return a build's state code.

    The v3 API reports the state as an object carrying a ``code``;
    reading it as a string never matched a known state.
    """
    state = details.get("state")
    if isinstance(state, dict):
        state = cast("dict[str, object]", state).get("code")
    return "" if state is None else str(state).lower()


def build_page(details: dict[str, object]) -> str:
    """Return the URL of a build's page on Read the Docs, when known."""
    urls = details.get("urls")
    if isinstance(urls, dict):
        page = cast("dict[str, object]", urls).get("build")
        if page:
            return str(page)
    return ""


def build_number(value: object) -> int | None:
    """Read a build identifier as a number, or None when it is not one."""
    try:
        return int(str(value))
    except ValueError:
        return None


def poll_build(
    client: SupportsReadTheDocs,
    target: Target,
    build_id: str,
    deadline: float,
    polling: Polling,
) -> dict[str, object]:
    """Wait for a build to end and return its final details."""
    while time.monotonic() < deadline:
        time.sleep(polling.interval)
        details = client.build_details(target.project, build_id)
        state = build_state(details)
        if details.get("success") is not None or state in _FINAL_STATES:
            return details
        log.info("Build %s in state %s; waiting", build_id, state or "unknown")

    msg = f"Read the Docs build {build_id} did not finish within {polling.timeout}s"
    raise ReadTheDocsError(msg)


def superseding_build(
    client: SupportsReadTheDocs, target: Target, build_id: str
) -> str:
    """Find the newest unfinished build of the same version.

    Read the Docs numbers builds in creation order, so a higher number
    means a build triggered after ours. It checks out the branch after
    our merge, so its result covers our change as well. An older build
    cannot, and a build of another version publishes something else.
    """
    ours = build_number(build_id)
    if ours is None:
        return ""

    newer = [
        number
        for entry in client.build_list(target.project)
        if str(entry.get("version", "")) == target.version
        and (number := build_number(entry.get("id"))) is not None
        and number > ours
    ]
    return str(max(newer)) if newer else ""


def publish(
    client: SupportsReadTheDocs,
    target: Target,
    polling: Polling,
    note: Callable[[str], None],
) -> str:
    """Trigger a build and return the identifier of the one that published.

    Read the Docs cancels a queued or running build when another is
    triggered for the same version, whether by a merge landing moments
    later or by the repository webhook firing for the same merge. A
    cancellation therefore says nothing about the documentation: this
    follows the build that superseded ours and reports its result, and
    triggers again only when no newer build is running.
    """
    where = f"{target.project}/{target.version}"
    deadline = time.monotonic() + polling.timeout
    build_id = client.build_trigger(target.project, target.version)
    triggered = 1
    note(f"Triggered build {build_id} of {where}")

    while True:
        details = poll_build(client, target, build_id, deadline, polling)

        if details.get("success") is True:
            note(f"Build {build_id} completed successfully")
            return build_id

        if build_state(details) != _CANCELLED:
            page = build_page(details)
            suffix = f": {page}" if page else ""
            msg = f"Read the Docs build {build_id} of {where} failed{suffix}"
            raise ReadTheDocsError(msg)

        newer = superseding_build(client, target, build_id)
        if newer:
            note(
                f"Build {build_id} was cancelled; build {newer} superseded it, "
                + "so its result stands for this change"
            )
            build_id = newer
            continue

        if triggered >= MAX_TRIGGERS:
            msg = (
                f"Read the Docs cancelled {triggered} builds of {where} "
                + "with no newer build to follow"
            )
            raise ReadTheDocsError(msg)

        # Polling a cancelled build, listing builds and API retries all
        # take time. A build triggered once the budget is spent would
        # time out on its first poll and run on unmonitored.
        if time.monotonic() >= deadline:
            msg = (
                f"Read the Docs cancelled build {build_id} of {where} with no "
                + f"time left within {polling.timeout}s to build again"
            )
            raise ReadTheDocsError(msg)

        build_id = client.build_trigger(target.project, target.version)
        triggered += 1
        note(f"Build was cancelled with no newer build running; triggered {build_id}")
