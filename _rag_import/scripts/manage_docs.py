"""
Automated Document Management & Re-Indexing Script

Usage:
  python scripts/manage_docs.py add --source /path/to/docs
  python scripts/manage_docs.py add --file doc1.md --file doc2.pdf
  python scripts/manage_docs.py list
  python scripts/manage_docs.py reindex --strategy all
  python scripts/manage_docs.py test --query "What is machine learning?"
  python scripts/manage_docs.py cleanup
  python scripts/manage_docs.py status
"""

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rag.config import settings
from rag.ingest.chunking import ChunkingStrategy
from rag.ingest.index import reset_chroma_collection
from rag.ingest.loaders import load_corpus
from rag.ingest.pipeline import run_ingest
from rag.retrieval.retriever import RetrievalConfig, hybrid_retrieve

# Supported document formats
SUPPORTED_FORMATS = {".md", ".txt", ".html", ".htm", ".pdf"}


def ensure_data_dir() -> None:
    """Create data/raw directory if it doesn't exist."""
    settings.raw_data_dir.mkdir(parents=True, exist_ok=True)
    settings.processed_data_dir.mkdir(parents=True, exist_ok=True)


def add_documents(source_paths: list[Path] | None = None, source_dir: Path | None = None) -> dict:
    """Add new documents to data/raw/."""
    ensure_data_dir()

    added_files = []
    skipped_files = []

    files_to_add = []

    # From source directory
    if source_dir:
        if not source_dir.exists():
            print(f"❌ Source directory not found: {source_dir}")
            return {"added": [], "skipped": []}

        for file_path in source_dir.iterdir():
            if file_path.suffix.lower() in SUPPORTED_FORMATS:
                files_to_add.append(file_path)

        if not files_to_add:
            print(f"❌ No supported documents found in {source_dir}")
            print(f"   Supported formats: {', '.join(SUPPORTED_FORMATS)}")
            return {"added": [], "skipped": []}

    # From explicit file paths
    if source_paths:
        for file_path in source_paths:
            if file_path.exists():
                files_to_add.append(file_path)
            else:
                skipped_files.append(f"{file_path} (not found)")

    # Copy files to data/raw/
    for file_path in files_to_add:
        if file_path.suffix.lower() not in SUPPORTED_FORMATS:
            skipped_files.append(f"{file_path.name} (unsupported format)")
            continue

        dest = settings.raw_data_dir / file_path.name

        if dest.exists():
            print(f"⚠️  {file_path.name} already exists, overwriting...")

        try:
            shutil.copy2(file_path, dest)
            added_files.append(file_path.name)
            print(f"✅ Added: {file_path.name}")
        except Exception as e:
            skipped_files.append(f"{file_path.name} (error: {str(e)})")
            print(f"❌ Failed to add {file_path.name}: {e}")

    return {
        "added": added_files,
        "skipped": skipped_files,
        "total_added": len(added_files),
        "total_skipped": len(skipped_files),
    }


def list_documents() -> None:
    """List all documents currently in data/raw/."""
    ensure_data_dir()

    documents = load_corpus(settings.raw_data_dir)

    if not documents:
        print("❌ No documents found in data/raw/")
        return

    print(f"\n📚 Total Documents: {len(documents)}\n")
    for i, doc in enumerate(documents, 1):
        print(f"{i}. {doc.title}")
        print(f"   File: {doc.metadata.get('filename', 'unknown')}")
        print(f"   Format: {doc.format}")
        print(f"   Size: {len(doc.text)} chars")
        print(f"   Path: {doc.source_path}")
        print()


def reindex_documents(strategy: str = "all", reset: bool = True) -> None:
    """Re-index all documents."""
    ensure_data_dir()

    documents = load_corpus(settings.raw_data_dir)
    if not documents:
        print("❌ No documents found in data/raw/")
        print("   Add documents first using: python scripts/manage_docs.py add")
        return

    print(f"\n🔄 Re-Indexing {len(documents)} documents...")
    print(f"   Reset indexes: {reset}")
    print(f"   Strategy: {strategy}\n")

    strategies = (
        list(ChunkingStrategy) if strategy == "all" else [ChunkingStrategy(strategy)]
    )

    results = []
    for strat in strategies:
        print(f"⏳ Processing strategy: {strat.value}")

        if reset:
            try:
                reset_chroma_collection(strat)
                print(f"   ✅ Reset ChromaDB collection")
            except Exception as e:
                print(f"   ⚠️  Could not reset collection: {e}")

        try:
            stats = run_ingest(strat, reset=reset)
            results.append(stats)

            print(f"   ✅ Ingestion complete")
            print(f"      - Documents: {stats['documents']}")
            print(f"      - Input chunks: {stats['input_chunks']}")
            print(f"      - Indexed chunks: {stats['indexed_chunks']}")
            print(f"      - Duplicates dropped: {stats['duplicates_dropped']}")
        except Exception as e:
            print(f"   ❌ Error during ingestion: {e}")
            import traceback
            traceback.print_exc()

    print("\n✅ Re-indexing complete!\n")


def test_retrieval(query: str, strategy: str = "structure_aware", top_k: int = 3) -> None:
    """Test retrieval with a sample query."""
    print(f"\n🔍 Testing retrieval with query: '{query}'\n")

    try:
        config = RetrievalConfig(
            strategy=ChunkingStrategy(strategy),
            top_k=top_k,
            use_reranker=False,  # Skip reranker for testing
        )
        results = hybrid_retrieve(query, config)

        if not results:
            print("❌ No results found!")
            return

        print(f"✅ Found {len(results)} results:\n")
        for i, chunk in enumerate(results, 1):
            print(f"{i}. {chunk.metadata.get('title', 'Unknown')}")
            print(f"   File: {chunk.metadata.get('filename', 'unknown')}")
            print(f"   Score: {chunk.score:.4f}")
            print(f"   Sources: {', '.join(chunk.sources)}")
            print(f"   Text: {chunk.text[:200]}...")
            print()

    except Exception as e:
        print(f"❌ Error during retrieval: {e}")
        import traceback
        traceback.print_exc()


def cleanup_old_indexes(keep_backup: bool = True) -> None:
    """Clean up old indexes and optionally keep backups."""
    print("\n🧹 Cleaning up old indexes...\n")

    chroma_dir = settings.chroma_persist_dir
    processed_dir = settings.processed_data_dir

    if keep_backup:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if chroma_dir.exists():
            backup_dir = chroma_dir.parent / f"chroma_backup_{timestamp}"
            shutil.copytree(chroma_dir, backup_dir)
            print(f"✅ Backed up ChromaDB to: {backup_dir}")

        for pkl_file in processed_dir.glob("bm25_*.pkl"):
            backup_path = pkl_file.parent / f"{pkl_file.stem}_backup_{timestamp}{pkl_file.suffix}"
            shutil.copy2(pkl_file, backup_path)
            print(f"✅ Backed up BM25 index: {backup_path}")

    print("\n✅ Cleanup complete!\n")


def show_status() -> None:
    """Show overall system status."""
    ensure_data_dir()

    print("\n📊 System Status\n")

    # Documents
    documents = load_corpus(settings.raw_data_dir)
    print(f"📄 Documents in data/raw/: {len(documents)}")
    for doc in documents:
        print(f"   - {doc.metadata.get('filename', 'unknown')} ({doc.format})")

    # ChromaDB collections
    print(f"\n🗂️  ChromaDB:")
    chroma_dir = settings.chroma_persist_dir
    if chroma_dir.exists():
        collections = [d.name for d in chroma_dir.iterdir() if d.is_dir()]
        print(f"   Collections: {len(collections)}")
        for collection in collections:
            print(f"   - {collection}")
    else:
        print("   No ChromaDB directory found")

    # BM25 indexes
    print(f"\n🔤 BM25 Indexes:")
    processed_dir = settings.processed_data_dir
    if processed_dir.exists():
        bm25_files = list(processed_dir.glob("bm25_*.pkl"))
        print(f"   Indexes: {len(bm25_files)}")
        for f in bm25_files:
            print(f"   - {f.name}")
    else:
        print("   No processed data directory found")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Automated document management and re-indexing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Add documents from a directory
  python scripts/manage_docs.py add --source /path/to/docs

  # Add specific files
  python scripts/manage_docs.py add --file doc1.md --file doc2.pdf

  # List all documents
  python scripts/manage_docs.py list

  # Re-index all documents
  python scripts/manage_docs.py reindex --strategy all

  # Test retrieval
  python scripts/manage_docs.py test --query "What is machine learning?"

  # Show system status
  python scripts/manage_docs.py status

  # Full workflow: add + reindex + test
  python scripts/manage_docs.py add --source ./new_docs
  python scripts/manage_docs.py reindex --strategy all
  python scripts/manage_docs.py test --query "Tell me about the new docs"
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # ADD command
    add_parser = subparsers.add_parser("add", help="Add new documents")
    add_group = add_parser.add_mutually_exclusive_group(required=True)
    add_group.add_argument("--source", type=Path, help="Directory containing documents to add")
    add_group.add_argument("--file", nargs="+", type=Path, help="Specific files to add")

    # LIST command
    subparsers.add_parser("list", help="List all documents")

    # REINDEX command
    reindex_parser = subparsers.add_parser("reindex", help="Re-index all documents")
    reindex_parser.add_argument(
        "--strategy",
        choices=[s.value for s in ChunkingStrategy] + ["all"],
        default="all",
        help="Chunking strategy to use",
    )
    reindex_parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Don't reset indexes before reindexing",
    )

    # TEST command
    test_parser = subparsers.add_parser("test", help="Test retrieval with a query")
    test_parser.add_argument("--query", required=True, help="Query to test")
    test_parser.add_argument(
        "--strategy",
        default="structure_aware",
        help="Chunking strategy to use",
    )
    test_parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Number of results to retrieve",
    )

    # CLEANUP command
    subparsers.add_parser("cleanup", help="Clean up old indexes (with backups)")

    # STATUS command
    subparsers.add_parser("status", help="Show system status")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Execute commands
    if args.command == "add":
        result = add_documents(
            source_paths=args.file,
            source_dir=args.source,
        )
        print(f"\n📊 Summary:")
        print(f"   Added: {result['total_added']}")
        print(f"   Skipped: {result['total_skipped']}")
        if result['added']:
            print(f"\n💡 Next step: python scripts/manage_docs.py reindex --strategy all")

    elif args.command == "list":
        list_documents()

    elif args.command == "reindex":
        reindex_documents(
            strategy=args.strategy,
            reset=not args.no_reset,
        )

    elif args.command == "test":
        test_retrieval(
            query=args.query,
            strategy=args.strategy,
            top_k=args.top_k,
        )

    elif args.command == "cleanup":
        cleanup_old_indexes(keep_backup=True)

    elif args.command == "status":
        show_status()


if __name__ == "__main__":
    main()
