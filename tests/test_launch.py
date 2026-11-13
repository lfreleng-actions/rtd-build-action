# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Tests for the launcher that runs the action's modules isolated."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LAUNCH = Path(__file__).resolve().parent.parent / "lib" / "launch.py"
PLANTED = "raise SystemExit(97)\n"


class LaunchTests(unittest.TestCase):
    """Run lib/launch.py the way action.yaml does."""

    def launch(
        self, cwd: str, *args: str, stdin: str = ""
    ) -> subprocess.CompletedProcess[str]:
        """Start the launcher isolated, with PYTHONPATH naming cwd."""
        env = dict(os.environ, PYTHONPATH=cwd)
        return subprocess.run(
            [sys.executable, "-I", str(LAUNCH), *args],
            cwd=cwd,
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_ignores_modules_in_the_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _ = (Path(cwd) / "json.py").write_text(PLANTED)
            _ = (Path(cwd) / "secrets.py").write_text(PLANTED)
            (Path(cwd) / "lib").mkdir()
            _ = (Path(cwd) / "lib" / "__init__.py").write_text(PLANTED)
            outcome = json.dumps({"project": "example-docs"})
            result = self.launch(cwd, "lib.emit_outputs", stdin=outcome)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("project=example-docs\n", result.stdout)

    def test_passes_arguments_to_the_module(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            result = self.launch(cwd, "lib.cli", "--mode", "publish")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice: 'publish'", result.stderr)

    def test_refuses_modules_outside_the_action(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            result = self.launch(cwd, "os")
        self.assertEqual(result.returncode, 1)
        self.assertIn("only the action's own modules", result.stderr)

    def test_requires_a_module(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            result = self.launch(cwd)
        self.assertEqual(result.returncode, 1)
        self.assertIn("usage:", result.stderr)


if __name__ == "__main__":
    _ = unittest.main()
