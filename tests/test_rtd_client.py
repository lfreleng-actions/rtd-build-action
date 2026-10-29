# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Tests for the Read the Docs client's handling of transient failures.

Replaces the subprocess call with scripted results, so the suite needs
no network access, no credentials and no lftools-uv installation.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.rtd_client import (  # noqa: E402 - path set above
    ReadTheDocsClient,
    ReadTheDocsError,
)

THROTTLED = (
    "Error: Build '35' of project 'onap-cps' failed with HTTP 429: "
    + "Request was throttled. Expected available in {} seconds."
)
SERVER_ERROR = "Error: Project 'onap-doc' failed with HTTP 500:\n<!DOCTYPE html>\n<title>Server error"


def failed(stderr: str) -> subprocess.CompletedProcess[str]:
    """A command that exited non-zero."""
    return subprocess.CompletedProcess([], 1, stdout="", stderr=stderr)


def answered(stdout: str) -> subprocess.CompletedProcess[str]:
    """A command that succeeded."""
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


class TransientFailures(unittest.TestCase):
    """Throttling and server errors are retried where that is safe."""

    def _client(
        self, *results: subprocess.CompletedProcess[str]
    ) -> tuple[ReadTheDocsClient, list[float], mock.MagicMock]:
        waits: list[float] = []
        for patcher in (
            mock.patch("lib.rtd_client.subprocess.run", side_effect=list(results)),
            mock.patch("time.sleep", side_effect=waits.append),
        ):
            _ = patcher.start()
            self.addCleanup(patcher.stop)
        runner = cast("mock.MagicMock", subprocess.run)
        return ReadTheDocsClient(), waits, runner

    def test_retries_a_throttled_read_after_the_hint(self) -> None:
        client, waits, runner = self._client(
            failed(THROTTLED.format(7)), answered('{"id": 35}')
        )
        self.assertEqual(client.build_details("onap-cps", "35"), {"id": 35})
        self.assertEqual(waits, [8])
        self.assertEqual(runner.call_count, 2)

    def test_retries_a_throttled_trigger(self) -> None:
        """A throttled request never ran, so even a trigger repeats safely."""
        client, _, runner = self._client(
            failed(THROTTLED.format(1)), answered('{"build": {"id": 36}}')
        )
        self.assertEqual(client.build_trigger("onap-cps", "latest"), "36")
        self.assertEqual(runner.call_count, 2)

    def test_caps_a_long_hint(self) -> None:
        client, waits, _ = self._client(
            failed(THROTTLED.format(600)), answered('{"id": 35}')
        )
        _ = client.build_details("onap-cps", "35")
        self.assertEqual(waits, [60])

    def test_retries_a_server_error_on_a_read(self) -> None:
        client, waits, runner = self._client(
            failed(SERVER_ERROR), answered('{"subprojects": ["onap-cps"]}')
        )
        self.assertEqual(client.subproject_list("onap-doc"), ["onap-cps"])
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(len(waits), 1)

    def test_never_repeats_a_trigger_after_a_server_error(self) -> None:
        """The first trigger may have queued a build before the error."""
        client, waits, runner = self._client(failed(SERVER_ERROR))
        with self.assertRaisesRegex(ReadTheDocsError, "HTTP 500"):
            _ = client.build_trigger("onap-cps", "latest")
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(waits, [])

    def test_never_repeats_a_creation_after_a_server_error(self) -> None:
        client, _, runner = self._client(failed(SERVER_ERROR))
        with self.assertRaises(ReadTheDocsError):
            _ = client.project_create("onap-cps", "https://gerrit.onap.org/r/cps", "")
        self.assertEqual(runner.call_count, 1)

    def test_gives_up_after_five_attempts(self) -> None:
        client, waits, runner = self._client(
            *[failed(THROTTLED.format(1)) for _ in range(5)]
        )
        with self.assertRaisesRegex(ReadTheDocsError, "HTTP 429"):
            _ = client.build_details("onap-cps", "35")
        self.assertEqual(runner.call_count, 5)
        self.assertEqual(len(waits), 4)

    def test_does_not_retry_other_client_errors(self) -> None:
        client, _, runner = self._client(
            failed("Error: Build trigger failed with HTTP 400: bad version")
        )
        with self.assertRaisesRegex(ReadTheDocsError, "HTTP 400"):
            _ = client.build_trigger("onap-cps", "latest")
        self.assertEqual(runner.call_count, 1)

    def test_stops_retrying_a_trigger_at_the_deadline(self) -> None:
        """A trigger landing after the deadline would go unmonitored."""
        client, waits, runner = self._client(
            failed(THROTTLED.format(10)), answered('{"build": {"id": 36}}')
        )
        with (
            mock.patch("time.monotonic", return_value=100.0),
            self.assertRaisesRegex(ReadTheDocsError, "HTTP 429"),
        ):
            _ = client.build_trigger("onap-cps", "latest", deadline=105.0)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(waits, [])

    def test_retries_a_trigger_within_the_deadline(self) -> None:
        client, waits, _ = self._client(
            failed(THROTTLED.format(10)), answered('{"build": {"id": 36}}')
        )
        with mock.patch("time.monotonic", return_value=100.0):
            build_id = client.build_trigger("onap-cps", "latest", deadline=200.0)
        self.assertEqual(build_id, "36")
        self.assertEqual(waits, [11])


if __name__ == "__main__":
    _ = unittest.main()
