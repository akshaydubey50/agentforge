"""
Quick Workflow Script: Add Documents + Reindex + Test

Usage (all-in-one):
  python scripts/quick_add_and_index.py --source ./new_docs --test "sample query"

This automates:
  1. Add documents from source directory
  2. Re-index with all strategies
  3. Test retrieval with sample query
  4. Show final status
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from manage_docs import add_documents, reindex_documents, test_retrieval, show_status


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-command workflow: Add documents + reindex + test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Add from directory and test
  python scripts/quick_add_and_index.py --source ./new_docs --test "What's new?"

  # Add specific files and test
  python scripts/quick_add_and_index.py --file doc1.md doc2.pdf --test "Tell me"

  # Add and use semantic strategy
  python scripts/quick_add_and_index.py --source ./docs --strategy semantic --test "Query"

  # Add and skip testing
  python scripts/quick_add_and_index.py --source ./docs --no-test
        """,
    )

    add_group = parser.add_mutually_exclusive_group(required=True)
    add_group.add_argument("--source", type=Path, help="Directory containing documents")
    add_group.add_argument("--file", nargs="+", type=Path, help="Specific files to add")

    parser.add_argument(
        "--test",
        type=str,
        help="Query to test retrieval (optional, use --no-test to skip)",
    )
    parser.add_argument(
        "--no-test",
        action="store_true",
        help="Skip retrieval testing",
    )
    parser.add_argument(
        "--strategy",
        choices=["fixed_overlap", "structure_aware", "semantic", "all"],
        default="all",
        help="Reindexing strategy (default: all)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Number of results for testing (default: 3)",
    )

    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  AUTOMATED DOCUMENT WORKFLOW")
    print("=" * 60)

    # Step 1: Add documents
    print("\n[1/4] Adding documents...")
    result = add_documents(
        source_paths=args.file,
        source_dir=args.source,
    )

    if result["total_added"] == 0:
        print("\n❌ No documents added. Aborting.")
        return

    print(f"\n✅ Added {result['total_added']} document(s)")

    # Step 2: Re-index
    print("\n[2/4] Re-indexing documents...")
    print(f"     Strategy: {args.strategy}")

    try:
        reindex_documents(strategy=args.strategy, reset=True)
        print("✅ Re-indexing complete")
    except Exception as e:
        print(f"❌ Re-indexing failed: {e}")
        return

    # Step 3: Test retrieval (optional)
    if args.no_test:
        print("\n[3/4] Testing skipped (--no-test)")
    elif args.test:
        print(f"\n[3/4] Testing retrieval...")
        print(f"     Query: '{args.test}'")
        print(f"     Strategy: {args.strategy if args.strategy != 'all' else 'structure_aware'}")
        print(f"     Top-K: {args.top_k}")

        test_strategy = args.strategy if args.strategy != "all" else "structure_aware"
        try:
            test_retrieval(
                query=args.test,
                strategy=test_strategy,
                top_k=args.top_k,
            )
            print("✅ Testing complete")
        except Exception as e:
            print(f"❌ Testing failed: {e}")
    else:
        print("\n[3/4] Testing skipped (no --test query provided)")

    # Step 4: Show status
    print("\n[4/4] Final system status...")
    show_status()

    print("\n" + "=" * 60)
    print("  ✅ WORKFLOW COMPLETE!")
    print("=" * 60)
    print("\n💡 Next steps:")
    print("   1. Start API server: uvicorn src.rag.main:app --host 0.0.0.0 --port 8000")
    print("   2. Start dashboard: streamlit run src/rag/dashboard.py")
    print("   3. Open: http://localhost:8501")
    print()


if __name__ == "__main__":
    main()
