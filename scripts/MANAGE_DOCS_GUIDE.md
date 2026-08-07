# Document Management Script Guide

## Overview

The `manage_docs.py` script automates the entire document workflow:
- ✅ Add new documents
- ✅ Re-index them
- ✅ Test retrieval
- ✅ Manage indexes
- ✅ Show status

## Commands

### 1️⃣ Add Documents

#### From a directory:
```bash
python scripts/manage_docs.py add --source C:\path\to\docs
```

Example:
```bash
python scripts/manage_docs.py add --source ./new_docs
```

#### Specific files:
```bash
python scripts/manage_docs.py add --file doc1.md doc2.pdf doc3.txt
```

**Supported formats:** `.md`, `.txt`, `.pdf`, `.html`, `.htm`

---

### 2️⃣ Re-Index Documents

#### All strategies:
```bash
python scripts/manage_docs.py reindex --strategy all
```

#### Single strategy:
```bash
python scripts/manage_docs.py reindex --strategy structure_aware
```

Options:
- `--strategy`: `fixed_overlap` | `structure_aware` | `semantic` | `all` (default: `all`)
- `--no-reset`: Keep old indexes and append (not recommended)

---

### 3️⃣ List All Documents

```bash
python scripts/manage_docs.py list
```

Shows:
- Total document count
- Title, format, size of each document
- File path

---

### 4️⃣ Test Retrieval

```bash
python scripts/manage_docs.py test --query "What is machine learning?"
```

Options:
- `--query`: Your test query (required)
- `--strategy`: Chunking strategy (default: `structure_aware`)
- `--top-k`: Number of results (default: 3)

Example:
```bash
python scripts/manage_docs.py test --query "Database optimization" --top-k 5
```

---

### 5️⃣ View System Status

```bash
python scripts/manage_docs.py status
```

Shows:
- Number of documents
- ChromaDB collections
- BM25 indexes

---

### 6️⃣ Cleanup Old Indexes

```bash
python scripts/manage_docs.py cleanup
```

Creates backups before cleaning:
- `chroma_backup_YYYYMMDD_HHMMSS/`
- `bm25_backup_*.pkl`

---

## Common Workflows

### Workflow 1: Add One New Document

```bash
# 1. Add document
python scripts/manage_docs.py add --file "my_doc.md"

# 2. Re-index
python scripts/manage_docs.py reindex --strategy all

# 3. Test it
python scripts/manage_docs.py test --query "topic from new doc"
```

---

### Workflow 2: Batch Add Documents

```bash
# 1. Create a folder with your docs
mkdir new_docs
cp *.md new_docs/
cp *.pdf new_docs/

# 2. Add all at once
python scripts/manage_docs.py add --source ./new_docs

# 3. Re-index
python scripts/manage_docs.py reindex --strategy all

# 4. List to verify
python scripts/manage_docs.py list
```

---

### Workflow 3: Complete Automation

```bash
# Full workflow in one go
python scripts/manage_docs.py add --source ./new_docs && \
python scripts/manage_docs.py reindex --strategy all && \
python scripts/manage_docs.py test --query "What's in the new docs?" && \
python scripts/manage_docs.py status
```

---

### Workflow 4: Development (Test Strategy)

```bash
# Test with expensive semantic strategy
python scripts/manage_docs.py reindex --strategy semantic

# Test retrieval
python scripts/manage_docs.py test --query "test query" --strategy semantic

# If good, keep it; if not, revert to faster strategy
python scripts/manage_docs.py reindex --strategy structure_aware
```

---

## What Each Command Does

### `add`
- ✅ Validates file formats
- ✅ Copies files to `data/raw/`
- ✅ Shows which files added/skipped
- ✅ Prompts next steps

### `list`
- ✅ Counts total documents
- ✅ Shows filename, format, size
- ✅ Lists file paths

### `reindex`
- ✅ Resets ChromaDB collections (or appends)
- ✅ Loads all documents from `data/raw/`
- ✅ Chunks using chosen strategy
- ✅ Drops near-duplicates
- ✅ Indexes in ChromaDB (dense)
- ✅ Builds BM25 (sparse)
- ✅ Shows statistics

### `test`
- ✅ Runs hybrid retrieval
- ✅ Shows top-k results
- ✅ Displays scores and sources
- ✅ No LLM reranking (fast)

### `status`
- ✅ Shows document count
- ✅ Lists ChromaDB collections
- ✅ Lists BM25 indexes

### `cleanup`
- ✅ Creates timestamped backups
- ✅ Optionally removes old data
- ✅ Safe operation

---

## Advanced Usage

### Only Re-Index Specific Strategy

```bash
# Only rebuild semantic index (slowest but best quality)
python scripts/manage_docs.py reindex --strategy semantic
```

### Keep Old Data (Append Mode)

```bash
# Add to existing indexes without resetting
# ⚠️ WARNING: May create duplicates!
python scripts/manage_docs.py reindex --strategy all --no-reset
```

### Test Different Strategies

```bash
# Test which strategy works best for your docs
python scripts/manage_docs.py test --query "sample query" --strategy fixed_overlap
python scripts/manage_docs.py test --query "sample query" --strategy structure_aware
python scripts/manage_docs.py test --query "sample query" --strategy semantic
```

---

## Troubleshooting

### Q: "No documents found in data/raw/"
```bash
# Check if directory exists
ls data/raw/

# Add a test document
echo "# Test" > data/raw/test.md

# Try again
python scripts/manage_docs.py add --file data/raw/test.md
```

### Q: "ModuleNotFoundError: No module named 'rag'"
```bash
# Activate venv
.venv\Scripts\activate

# Install package
pip install -e .

# Run again
python scripts/manage_docs.py status
```

### Q: "Could not reach API"
Make sure your API server is running:
```bash
# In another terminal
uvicorn src.rag.main:app --host 0.0.0.0 --port 8000
```

### Q: "OPENAI_API_KEY is not set"
```bash
# Check .env file
cat .env

# Add key if missing
echo "OPENAI_API_KEY=sk-..." >> .env
```

---

## Environment Setup

Before first use:

```bash
# Activate virtual environment
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
pip install -e .

# Check API key
cat .env

# Verify setup
python scripts/manage_docs.py status
```

---

## API Integration

If your API is running, documents are automatically available:

```bash
# Query the API
curl http://localhost:8000/v1/documents

# Ingest via API
curl -X POST http://localhost:8000/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"strategy": "all"}'
```

---

## File Structure

```
data/
├── raw/                      ← Your documents go here
│   ├── doc1.md
│   ├── doc2.pdf
│   └── doc3.txt
│
├── chroma/                   ← Dense search indexes
│   ├── rag_structure_aware/
│   ├── rag_fixed_overlap/
│   └── rag_semantic/
│
└── processed/                ← Sparse search indexes
    ├── bm25_structure_aware.pkl
    ├── bm25_meta_structure_aware.json
    └── ...
```

---

## Quick Command Reference

```bash
# Add docs
python scripts/manage_docs.py add --source ./docs

# Reindex
python scripts/manage_docs.py reindex --strategy all

# Test
python scripts/manage_docs.py test --query "sample"

# List
python scripts/manage_docs.py list

# Status
python scripts/manage_docs.py status

# Cleanup
python scripts/manage_docs.py cleanup
```

---

That's it! All document management is now automated. 🚀
