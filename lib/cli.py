# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Command line for the Read the Docs build action."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import cast

from lib.rtd_build import MODE_MERGE, MODE_VERIFY, Settings, run
from lib.rtd_client import (
    ClientConfig,
    NotFoundError,
    ReadTheDocsClient,
    ReadTheDocsError,
)
from lib.rtd_naming import NamingError


def build_parser() -> argparse.ArgumentParser:
    """Define the command line."""
    parser = argparse.ArgumentParser(description="Drive the Read the Docs API.")
    _ = parser.add_argument(
        "--mode", default=MODE_VERIFY, choices=(MODE_VERIFY, MODE_MERGE)
    )
    _ = parser.add_argument("--gerrit-project", default="", help="Gerrit project path")
    _ = parser.add_argument("--gerrit-change-url", default="", help="Gerrit change URL")
    _ = parser.add_argument("--branch", default="", help="Branch under change")
    _ = parser.add_argument(
        "--default-branch",
        default="",
        help="Branch Read the Docs publishes as the default version; empty accepts master or main",
    )
    _ = parser.add_argument("--project", default="", help="Read the Docs project slug")
    _ = parser.add_argument(
        "--parent-project", default="", help="Umbrella project slug"
    )
    _ = parser.add_argument(
        "--parent-suffix", default="doc", help="Umbrella docs suffix"
    )
    _ = parser.add_argument("--project-overrides", default="", help="from=to rewrites")
    _ = parser.add_argument(
        "--default-version", default="latest", help="Landing version"
    )
    _ = parser.add_argument(
        "--repository-url", default="", help="Repository URL for creation"
    )
    _ = parser.add_argument("--homepage", default="", help="Homepage URL for creation")
    _ = parser.add_argument("--build-timeout", type=int, default=1800)
    _ = parser.add_argument("--poll-interval", type=int, default=10)
    _ = parser.add_argument("--create-timeout", type=int, default=600)
    _ = parser.add_argument(
        "--lftools-version", default="", help="lftools-uv version to run"
    )
    _ = parser.add_argument(
        "--launcher", default="uvx", help="Tool that runs lftools-uv"
    )
    return parser


def settings_from(namespace: argparse.Namespace) -> tuple[Settings, ClientConfig]:
    """Convert parsed arguments into typed settings."""
    values = cast("dict[str, object]", vars(namespace))

    def text(key: str) -> str:
        value = values.get(key)
        return "" if value is None else str(value)

    def number(key: str, fallback: int) -> int:
        value = values.get(key)
        return value if isinstance(value, int) else fallback

    settings = Settings(
        mode=text("mode"),
        gerrit_project=text("gerrit_project"),
        gerrit_change_url=text("gerrit_change_url"),
        branch=text("branch"),
        default_branch=text("default_branch"),
        project=text("project"),
        parent_project=text("parent_project"),
        parent_suffix=text("parent_suffix"),
        project_overrides=text("project_overrides"),
        default_version=text("default_version"),
        repository_url=text("repository_url"),
        homepage=text("homepage"),
        build_timeout=number("build_timeout", 1800),
        poll_interval=number("poll_interval", 10),
        create_timeout=number("create_timeout", 600),
    )
    config = ClientConfig(
        version=text("lftools_version"), launcher=text("launcher") or "uvx"
    )
    return settings, config


def main(argv: list[str] | None = None) -> int:
    """Run the requested lane and print a JSON report to stdout."""
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    settings, config = settings_from(build_parser().parse_args(argv))

    try:
        outcome = run(settings, ReadTheDocsClient(config))
    except (NamingError, NotFoundError, ReadTheDocsError) as exc:
        _ = sys.stderr.write(f"Error: {exc}\n")
        return 1

    print(json.dumps(outcome.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
