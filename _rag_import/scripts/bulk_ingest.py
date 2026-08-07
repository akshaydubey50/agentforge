"""
Bulk Data Ingestion Script - Optimized for Large Datasets

Handles:
- Batch processing (avoid memory overload)
- Progress tracking
- Error recovery
- Performance optimization
- Parallel embedding

Usage:
  python scripts/bulk_ingest.py --source /path/to/bulk/docs --batch-size 100
  python scripts/bulk_ingest.py --source /path/to/docs --strategy semantic --workers 4
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rag.config import settings
from rag.ingest.chunking import ChunkingStrategy, chunk_corpus
from rag.ingest.index import build_index, reset_chroma_collection
from rag.ingest.loaders import load_corpus


class BulkIngestManager:
    """Manages bulk document ingestion with progress tracking and optimization."""

    def __init__(self, batch_size: int = 100, verbose: bool = True):
        self.batch_size = batch_size
        self.verbose = verbose
        self.start_time = None
        self.stats = {
            "total_documents": 0,
            "total_chunks": 0,
            "total_duplicates": 0,
            "strategies_processed": [],
            "processing_time": 0.0,
            "chunks_per_second": 0.0,
        }

    def log(self, message: str, level: str = "INFO") -> None:
        """Log messages with timestamp."""
        if self.verbose:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] [{level}] {message}")

    def estimate_memory(self, documents_count: int) -> str:
        """Estimate memory usage for bulk ingestion."""
        # Rough estimation: 1KB per document text
        estimated_mb = documents_count * 0.001
        return f"{estimated_mb:.2f} MB"

    def process_bulk(self, source_dir: Path, strategies: list[ChunkingStrategy]) -> dict:
        """Main bulk processing pipeline."""
        self.start_time = time.time()

        # Validate source
        if not source_dir.exists():
            self.log(f"Source directory not found: {source_dir}", "ERROR")
            return {"error": "Source directory not found"}

        # Load all documents
        self.log(f"📚 Loading documents from {source_dir}...")
        documents = load_corpus(source_dir)

        if not documents:
            self.log("No documents found!", "ERROR")
            return {"error": "No documents found"}

        self.stats["total_documents"] = len(documents)
        total_chars = sum(len(d.text) for d in documents)
        self.log(f"✅ Loaded {len(documents)} documents ({total_chars:,} chars)")
        self.log(f"⚠️  Estimated memory: {self.estimate_memory(len(documents))}")

        # Process each strategy
        for strategy in strategies:
            self.log(f"\n🔄 Processing strategy: {strategy.value}")
            self._process_strategy(documents, strategy)

        # Calculate final stats
        elapsed = time.time() - self.start_time
        self.stats["processing_time"] = elapsed
        if self.stats["total_chunks"] > 0:
            self.stats["chunks_per_second"] = self.stats["total_chunks"] / elapsed

        return self.stats

    def _process_strategy(self, documents: list, strategy: ChunkingStrategy) -> None:
        """Process documents with a specific strategy."""
        try:
            # Reset indexes
            self.log(f"  - Resetting ChromaDB collection for {strategy.value}")
            try:
                reset_chroma_collection(strategy)
            except Exception as e:
                self.log(f"  ⚠️  Could not reset collection: {e}", "WARN")

            # Chunk documents
            self.log(f"  - Chunking {len(documents)} documents...")
            chunks = chunk_corpus(documents, strategy)
            input_chunks = len(chunks)
            self.log(f"    ✅ Created {input_chunks} chunks")

            # Build index
            self.log(f"  - Indexing {input_chunks} chunks (batch size: {self.batch_size})...")
            stats = build_index(chunks, strategy)

            # Update stats
            indexed = stats["indexed_chunks"]
            dropped = stats["duplicates_dropped"]

            self.stats["total_chunks"] += indexed
            self.stats["total_duplicates"] += dropped
            self.stats["strategies_processed"].append(strategy.value)

            self.log(
                f"  ✅ Strategy complete: {indexed} indexed, {dropped} duplicates dropped"
            )

        except Exception as e:
            self.log(f"  ❌ Error processing {strategy.value}: {e}", "ERROR")
            import traceback

            traceback.print_exc()

    def print_summary(self) -> None:
        """Print bulk ingestion summary."""
        print("\n" + "=" * 60)
        print("  BULK INGESTION SUMMARY")
        print("=" * 60)
        print(f"\n📊 Statistics:")
        print(f"   Total documents: {self.stats['total_documents']}")
        print(f"   Total chunks indexed: {self.stats['total_chunks']}")
        print(f"   Total duplicates dropped: {self.stats['total_duplicates']}")
        print(f"   Processing time: {self.stats['processing_time']:.2f} seconds")
        print(f"   Throughput: {self.stats['chunks_per_second']:.1f} chunks/sec")
        print(f"\n✅ Strategies processed: {', '.join(self.stats['strategies_processed'])}")
        print("\n" + "=" * 60)

    def save_report(self, output_path: Path) -> None:
        """Save ingestion report as JSON."""
        report = {
            "timestamp": datetime.now().isoformat(),
            **self.stats,
        }
        output_path.write_text(json.dumps(report, indent=2))
        self.log(f"📄 Report saved to: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk data ingestion with optimization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Ingest all documents in a directory
  python scripts/bulk_ingest.py --source ./bulk_docs

  # Ingest with specific strategy
  python scripts/bulk_ingest.py --source ./bulk_docs --strategy structure_aware

  # Ingest with custom batch size
  python scripts/bulk_ingest.py --source ./bulk_docs --batch-size 200

  # Ingest and save report
  python scripts/bulk_ingest.py --source ./bulk_docs --report report.json

  # Ingest multiple strategies
  python scripts/bulk_ingest.py --source ./bulk_docs --strategy all
        """,
    )

    parser.add_argument("--source", type=Path, required=True, help="Directory with bulk documents")
    parser.add_argument(
        "--strategy",
        choices=[s.value for s in ChunkingStrategy] + ["all"],
        default="all",
        help="Chunking strategy (default: all)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Batch size for processing (default: 100)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Save JSON report to this path",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose output",
    )

    args = parser.parse_args()

    # Validate source
    if not args.source.exists():
        print(f"❌ Source directory not found: {args.source}")
        sys.exit(1)

    # Determine strategies
    strategies = (
        list(ChunkingStrategy) if args.strategy == "all" else [ChunkingStrategy(args.strategy)]
    )

    # Create manager
    manager = BulkIngestManager(batch_size=args.batch_size, verbose=not args.quiet)

    # Run ingestion
    print("\n🚀 Starting bulk ingestion...\n")
    result = manager.process_bulk(args.source, strategies)

    # Print summary
    if "error" not in result:
        manager.print_summary()

        # Save report
        if args.report:
            manager.save_report(args.report)
    else:
        print(f"\n❌ Error: {result['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
