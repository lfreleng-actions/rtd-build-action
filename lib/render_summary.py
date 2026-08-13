# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Render a Read the Docs outcome as a GitHub step summary.

Reads the JSON outcome on stdin and writes Markdown on stdout.
"""

from __future__ import annotations

import json
import sys
from typing import cast

HEADING = "### ReadTheDocs"


def text_of(outcome: dict[str, object], key: str, fallback: str = "") -> str:
    """Read an outcome field as text, substituting a fallback when empty."""
    value = outcome.get(key)
    if value is None or value == "":
        return fallback
    return str(value)


def flag(outcome: dict[str, object], key: str) -> str:
    """Render a boolean field as a tick or a dash."""
    return "yes" if outcome.get(key) is True else "no"


def property_rows(outcome: dict[str, object]) -> list[str]:
    """Build the property table for an outcome."""
    rows = [
        "| Property | Value |",
        "| -------- | ----- |",
        f"| Mode | `{flatten(text_of(outcome, 'mode'))}` |",
        f"| Project | `{flatten(text_of(outcome, 'project'))}` |",
        f"| Umbrella | `{flatten(text_of(outcome, 'parent_project'))}` |",
        f"| Version | `{flatten(text_of(outcome, 'version_slug'))}` |",
        f"| Project created | {flag(outcome, 'project_created')} |",
        f"| Subproject attached | {flag(outcome, 'subproject_created')} |",
        f"| Version activated | {flag(outcome, 'version_activated')} |",
    ]
    build_id = flatten(text_of(outcome, "build_id"))
    if build_id:
        rows.append(f"| Build | `{build_id}` ({flag(outcome, 'build_success')}) |")
    rows.append("")
    return rows


def flatten(text: str) -> str:
    """Collapse a message onto one line.

    An error surfaced from the API can span several lines, and a raw
    newline inside a list item ends the item.
    """
    return " ".join(text.split())


def note_lines(outcome: dict[str, object]) -> list[str]:
    """Build the notes list for an outcome."""
    raw = outcome.get("notes")
    if not isinstance(raw, list) or not raw:
        return []
    lines = ["<details><summary>Details</summary>", ""]
    lines += [f"- {flatten(str(note))}" for note in cast("list[object]", raw)]
    lines += ["", "</details>", ""]
    return lines


def render(outcome: dict[str, object]) -> str:
    """Build the Markdown summary for an outcome."""
    url = text_of(outcome, "documentation_url")
    lines = [HEADING, ""]
    if url:
        lines += [f"Documentation: {url}", ""]
    lines += property_rows(outcome)
    lines += note_lines(outcome)
    return "\n".join(lines)


def main() -> int:
    """Read an outcome from stdin and print its summary."""
    try:
        loaded = cast("object", json.load(sys.stdin))
    except json.JSONDecodeError:
        print(f"{HEADING}\n\nThe lane produced no readable outcome.\n")
        return 0
    if not isinstance(loaded, dict):
        print(f"{HEADING}\n\nThe lane produced no readable outcome.\n")
        return 0
    print(render(cast("dict[str, object]", loaded)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
