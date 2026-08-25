"""CLI: python scripts/run_gate.py   (or: make gate)

The ship / no-ship check. Exit 0 means safe to release. See
src/agentsys/eval/gate.py for what the three tiers are and why only the last
one is held to a threshold rather than a boolean.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentsys.eval.gate import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
