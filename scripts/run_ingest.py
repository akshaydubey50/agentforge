"""CLI: python scripts/run_ingest.py --strategy structure_aware
       python scripts/run_ingest.py --strategy all
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rag.ingest.chunking import ChunkingStrategy  # noqa: E402
from rag.ingest.pipeline import run_ingest  # noqa: E402


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
    for strategy in strategies:
        print(f"\n== Ingesting with strategy: {strategy.value} ==")
        stats = run_ingest(strategy)
        for k, v in stats.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
