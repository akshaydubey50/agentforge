"""
Debug script to troubleshoot UI issues

Usage:
  python scripts/debug_ui.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests

from rag.config import settings
from rag.ingest.loaders import load_corpus

print("\n" + "=" * 60)
print("  UI DEBUGGING SCRIPT")
print("=" * 60)

# 1. Check data directory
print("\n[1] Checking data directory...")
print(f"   Raw data dir: {settings.raw_data_dir}")
print(f"   Exists: {settings.raw_data_dir.exists()}")

if settings.raw_data_dir.exists():
    files = list(settings.raw_data_dir.iterdir())
    print(f"   Documents found: {len(files)}")
    for f in files:
        print(f"     - {f.name}")
else:
    print("   [ERROR] Directory does not exist!")

# 2. Check documents can be loaded
print("\n[2] Checking if documents can be loaded...")
try:
    documents = load_corpus(settings.raw_data_dir)
    print(f"   [OK] Loaded {len(documents)} documents")
    for doc in documents:
        print(f"     - {doc.title} ({doc.format})")
except Exception as e:
    print(f"   [ERROR] Error loading documents: {e}")

# 3. Check API connection
print("\n[3] Checking API connection...")
api_url = "http://localhost:8000"
try:
    response = requests.get(f"{api_url}/health", timeout=5)
    print(f"   ✅ API is running at {api_url}")
    print(f"      Status: {response.json()}")
except requests.ConnectionError:
    print(f"   ❌ Cannot connect to API at {api_url}")
    print("      → Start API: uvicorn src.rag.main:app --host 0.0.0.0 --port 8000")
except Exception as e:
    print(f"   ❌ Error: {e}")

# 4. Check API documents endpoint
print("\n[4] Checking API /v1/documents endpoint...")
try:
    response = requests.get(f"{api_url}/v1/documents", timeout=5)
    docs = response.json()
    if isinstance(docs, list):
        print(f"   ✅ API returned {len(docs)} documents")
        for doc in docs:
            print(f"     - {doc.get('title', 'Unknown')} ({doc.get('format', 'unknown')})")
    else:
        print(f"   ⚠️  Unexpected response: {docs}")
except requests.ConnectionError:
    print(f"   ❌ API not running")
except Exception as e:
    print(f"   ❌ Error: {e}")

# 5. Check indexes
print("\n[5] Checking indexes...")
chroma_dir = settings.chroma_persist_dir
processed_dir = settings.processed_data_dir

print(f"   ChromaDB dir: {chroma_dir}")
if chroma_dir.exists():
    collections = [d.name for d in chroma_dir.iterdir() if d.is_dir()]
    print(f"   ✅ Collections: {len(collections)}")
    for c in collections:
        print(f"      - {c}")
else:
    print(f"   ⚠️  ChromaDB directory not found")

print(f"\n   Processed data dir: {processed_dir}")
if processed_dir.exists():
    bm25_files = list(processed_dir.glob("bm25_*.pkl"))
    print(f"   ✅ BM25 indexes: {len(bm25_files)}")
    for f in bm25_files:
        print(f"      - {f.name}")
else:
    print(f"   ⚠️  Processed data directory not found")

# 6. Recommendations
print("\n[6] Troubleshooting Recommendations:")
print()

api_running = False
try:
    requests.get(f"{api_url}/health", timeout=2)
    api_running = True
except:
    pass

docs_count = len(load_corpus(settings.raw_data_dir))

if not api_running:
    print("   ❌ API is NOT running")
    print("      → Run in Terminal 1:")
    print("        uvicorn src.rag.main:app --host 0.0.0.0 --port 8000")
    print()

if docs_count == 0:
    print("   ❌ No documents in data/raw/")
    print("      → Add documents:")
    print("        python scripts/manage_docs.py add --source ./new_docs")
    print()

if not Path(settings.chroma_persist_dir).exists() or not list(Path(settings.chroma_persist_dir).glob("rag_*")):
    print("   ❌ ChromaDB indexes not found")
    print("      → Reindex documents:")
    print("        python scripts/manage_docs.py reindex --strategy all")
    print()

print("\n" + "=" * 60)
print("  QUICK FIX CHECKLIST")
print("=" * 60)
print()
print("1. Is API running?")
print(f"   {'✅ YES' if api_running else '❌ NO'}")
print()
print("2. Are documents in data/raw/?")
print(f"   {'✅ YES' if docs_count > 0 else '❌ NO'} ({docs_count} documents)")
print()
print("3. Are indexes built?")
chroma_exists = Path(settings.chroma_persist_dir).exists() and any(Path(settings.chroma_persist_dir).glob("rag_*"))
print(f"   {'✅ YES' if chroma_exists else '❌ NO'}")
print()

print("=" * 60)
print()

# Final instruction
if api_running and docs_count > 0 and chroma_exists:
    print("✅ Everything looks good! Restart Streamlit:")
    print()
    print("   streamlit run src/rag/upload_dashboard.py")
    print()
    print("   Then in browser, press Ctrl+R to refresh cache")
    print()
else:
    print("⚠️  Fix the issues above, then try again")
    print()
