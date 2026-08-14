# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Translate an outcome into GitHub Actions output assignments.

Reads the JSON outcome on stdin and writes ``key=value`` lines on stdout,
which the calling action appends to ``$GITHUB_OUTPUT``. The complete
outcome travels as a heredoc block so that its newlines cannot inject
further keys.
"""

from __future__ import annotations

import json
import secrets
import sys
from typing import cast

#: Outputs the action exposes as plain scalars.
SCALAR_KEYS = (
    "project",
    "parent_project",
    "version_slug",
    "project_exists",
    "project_created",
    "subproject_created",
    "version_activated",
    "build_id",
    "build_success",
    "documentation_url",
)


def scalar(value: object) -> str:
    """Render an outcome value as a single-line output string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).replace("\n", " ").replace("\r", " ")


def heredoc(name: str, payload: str) -> list[str]:
    """Emit a multi-line output using a delimiter absent from the payload."""
    delimiter = f"ghadelim_{secrets.token_hex(16)}"
    while delimiter in payload:
        delimiter = f"ghadelim_{secrets.token_hex(16)}"
    return [f"{name}<<{delimiter}", payload, delimiter]


def render(outcome: dict[str, object], raw: str) -> list[str]:
    """Build every output assignment for an outcome."""
    lines = [f"{key}={scalar(outcome.get(key))}" for key in SCALAR_KEYS]
    lines += heredoc("outcome_json", raw)
    return lines


def main() -> int:
    """Read an outcome from stdin and print its output assignments."""
    raw = sys.stdin.read()
    try:
        loaded = cast("object", json.loads(raw))
    except json.JSONDecodeError:
        _ = sys.stderr.write("Error: the lane produced no readable outcome\n")
        return 1
    if not isinstance(loaded, dict):
        _ = sys.stderr.write("Error: the outcome is not an object\n")
        return 1

    for line in render(cast("dict[str, object]", loaded), raw):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
