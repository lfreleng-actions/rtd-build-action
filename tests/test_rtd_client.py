# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Tests for the Read the Docs client's handling of failed commands.

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
    THROTTLE_BUDGET,
    NotFoundError,
    ReadTheDocsClient,
    ReadTheDocsError,
    TransientError,
)

THROTTLED = (
    "Error: Build '35' of project 'onap-cps' failed with HTTP 429: "
    + "Request was throttled. Expected available in {} seconds."
)
SERVER_ERROR = "Error: Project 'onap-doc' failed with HTTP 500:\n<!DOCTYPE html>\n<title>Server error"


def failed(stderr: str, status: int = 1) -> subprocess.CompletedProcess[str]:
    """A command that exited non-zero."""
    return subprocess.CompletedProcess([], status, stdout="", stderr=stderr)


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

    def test_keeps_retrying_throttling_within_the_budget(self) -> None:
        """A merge burst throttles for longer than five short hints."""
        client, waits, runner = self._client(
            *[failed(THROTTLED.format(9)) for _ in range(7)], answered('{"id": 35}')
        )
        self.assertEqual(client.build_details("onap-cps", "35"), {"id": 35})
        self.assertEqual(runner.call_count, 8)
        self.assertEqual(waits, [10] * 7)

    def test_gives_up_throttling_past_the_budget(self) -> None:
        hint = 59
        attempts = THROTTLE_BUDGET // (hint + 1) + 1
        client, waits, runner = self._client(
            *[failed(THROTTLED.format(hint)) for _ in range(attempts)],
            answered('{"id": 35}'),
        )
        with self.assertRaisesRegex(TransientError, "HTTP 429"):
            _ = client.build_details("onap-cps", "35")
        self.assertEqual(runner.call_count, attempts)
        self.assertLessEqual(sum(waits), THROTTLE_BUDGET)

    def test_gives_up_after_five_attempts(self) -> None:
        """Server errors keep an attempt limit; only throttling has a budget."""
        client, waits, runner = self._client(
            *[failed(SERVER_ERROR) for _ in range(5)], answered('{"id": 35}')
        )
        with self.assertRaisesRegex(TransientError, "HTTP 500"):
            _ = client.build_details("onap-cps", "35")
        self.assertEqual(runner.call_count, 5)
        self.assertEqual(len(waits), 4)

    def test_does_not_retry_other_client_errors(self) -> None:
        client, _, runner = self._client(
            failed("Error: Build trigger failed with HTTP 400: bad version")
        )
        with self.assertRaisesRegex(ReadTheDocsError, "HTTP 400") as caught:
            _ = client.build_trigger("onap-cps", "latest")
        self.assertNotIsInstance(caught.exception, TransientError)
        self.assertEqual(runner.call_count, 1)

    def test_stops_retrying_a_trigger_at_the_deadline(self) -> None:
        """A trigger landing after the deadline would go unmonitored."""
        client, waits, runner = self._client(
            failed(THROTTLED.format(10)), answered('{"build": {"id": 36}}')
        )
        with (
            mock.patch("time.monotonic", return_value=100.0),
            self.assertRaisesRegex(TransientError, "HTTP 429"),
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


class MissingResources(unittest.TestCase):
    """Only lftools-uv's exact 404 message for the resource means absent.

    The messages are those lftools-uv 0.8.0 prints on stderr, exiting 1.
    """

    def _client(
        self, *results: subprocess.CompletedProcess[str]
    ) -> tuple[ReadTheDocsClient, mock.MagicMock]:
        for patcher in (
            mock.patch("lib.rtd_client.subprocess.run", side_effect=list(results)),
            mock.patch("time.sleep"),
        ):
            _ = patcher.start()
            self.addCleanup(patcher.stop)
        runner = cast("mock.MagicMock", subprocess.run)
        return ReadTheDocsClient(), runner

    def test_a_missing_project_is_absent(self) -> None:
        client, _ = self._client(failed("Error: Project 'onap-doc' not found"))
        self.assertFalse(client.project_exists("onap-doc"))

    def test_a_missing_project_with_a_detail_is_absent(self) -> None:
        client, _ = self._client(
            failed(
                "Error: Project 'onap-doc' not found: "
                + "No Project matches the given query."
            )
        )
        self.assertFalse(client.project_exists("onap-doc"))

    def test_a_missing_version_is_absent(self) -> None:
        client, _ = self._client(
            failed("Error: Version 'latest' of project 'onap-doc' not found")
        )
        self.assertIsNone(client.version_details("onap-doc", "latest"))

    def test_a_missing_version_with_a_detail_is_absent(self) -> None:
        client, _ = self._client(
            failed(
                "warning: a dependency spoke up\n"
                + "Error: Version 'latest' of project 'onap-doc' not found: "
                + "Not found."
            )
        )
        self.assertIsNone(client.version_details("onap-doc", "latest"))

    def test_an_unauthorised_lookup_is_an_error(self) -> None:
        client, _ = self._client(
            failed("Error: Project 'onap-doc' failed with HTTP 401: Invalid token.")
        )
        with self.assertRaisesRegex(ReadTheDocsError, "HTTP 401") as caught:
            _ = client.project_exists("onap-doc")
        self.assertNotIsInstance(caught.exception, NotFoundError)

    def test_a_server_error_is_an_error(self) -> None:
        client, _ = self._client(
            *[
                failed(
                    "Error: Version 'latest' of project 'onap-doc' "
                    + "failed with HTTP 502"
                )
                for _ in range(5)
            ]
        )
        with self.assertRaisesRegex(TransientError, "HTTP 502"):
            _ = client.version_details("onap-doc", "latest")

    def test_another_missing_project_is_an_error(self) -> None:
        client, _ = self._client(failed("Error: Project 'onap' not found"))
        with self.assertRaises(ReadTheDocsError) as caught:
            _ = client.project_exists("onap-doc")
        self.assertNotIsInstance(caught.exception, NotFoundError)

    def test_a_missing_project_is_not_a_missing_version(self) -> None:
        client, _ = self._client(failed("Error: Project 'onap-doc' not found"))
        with self.assertRaises(ReadTheDocsError) as caught:
            _ = client.version_details("onap-doc", "latest")
        self.assertNotIsInstance(caught.exception, NotFoundError)

    def test_a_launcher_failure_is_an_error(self) -> None:
        client, _ = self._client(
            failed(
                "error: Executable `lftools-uv` not found in package `lftools-uv`",
                status=2,
            )
        )
        with self.assertRaises(ReadTheDocsError) as caught:
            _ = client.project_exists("onap-doc")
        self.assertNotIsInstance(caught.exception, NotFoundError)

    def test_a_quoted_not_found_is_an_error(self) -> None:
        client, _ = self._client(
            failed(
                "Error: Project 'onap-doc' failed with HTTP 400: "
                + "Error: Project 'onap-doc' not found"
            )
        )
        with self.assertRaises(ReadTheDocsError) as caught:
            _ = client.project_exists("onap-doc")
        self.assertNotIsInstance(caught.exception, NotFoundError)

    def test_a_not_found_with_another_status_is_an_error(self) -> None:
        client, _ = self._client(
            failed("Error: Project 'onap-doc' not found", status=2)
        )
        with self.assertRaises(ReadTheDocsError) as caught:
            _ = client.project_exists("onap-doc")
        self.assertNotIsInstance(caught.exception, NotFoundError)


if __name__ == "__main__":
    _ = unittest.main()
