"""CLI: python scripts/run_eval.py --strategy all
       python scripts/run_eval.py --strategy structure_aware
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rag.eval.golden_dataset import load_golden_dataset  # noqa: E402
from rag.eval.report import compare_strategies  # noqa: E402
from rag.ingest.chunking import ChunkingStrategy  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--strategy",
        choices=[s.value for s in ChunkingStrategy] + ["all"],
        default="all",
    )
    args = parser.parse_args()

    strategies = (
        list(ChunkingStrategy) if args.strategy == "all" else [ChunkingStrategy(args.strategy)]
    )
    cases = load_golden_dataset()
    print(f"Loaded {len(cases)} golden cases. Running against strategies: "
          f"{[s.value for s in strategies]}")

    start = time.time()
    reports = compare_strategies(strategies=strategies, cases=cases)
    elapsed = time.time() - start

    for report in reports:
        print(f"\n== {report.strategy} ==")
        for k, v in report.aggregates.items():
            print(f"  {k}: {v}")

    print(f"\nDone in {elapsed:.1f}s. Results in data/eval/results/")


if __name__ == "__main__":
    main()
