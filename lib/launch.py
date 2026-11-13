# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Run one of the action's modules under an isolated interpreter.

``python -I`` leaves the working directory, the script's directory and
PYTHONPATH off ``sys.path``, so ``python -I -m lib.cli`` cannot find the
package. Started as ``python -I lib/launch.py MODULE ARGS...``, this
script puts the action's root, and nothing else, ahead of the standard
library path and runs MODULE as ``__main__``.
"""

from __future__ import annotations

import os
import runpy
import sys


def main() -> None:
    """Run the module named by the first argument."""
    if len(sys.argv) < 2:
        sys.exit("usage: launch.py MODULE [ARGS...]")
    module = sys.argv.pop(1)
    if module != "lib" and not module.startswith("lib."):
        sys.exit(f"launch.py runs only the action's own modules, not {module}")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    _ = runpy.run_module(module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
