# Document Management Scripts

## 📋 Overview

These scripts automate the entire document lifecycle - no more manual steps!

**What's automated:**
- ✅ Add new documents
- ✅ Copy to correct directory
- ✅ Validate formats
- ✅ Run ingestion
- ✅ Create indexes (ChromaDB + BM25)
- ✅ Test retrieval
- ✅ Manage backups
- ✅ Show status

---

## 📚 Available Scripts

### 1. **manage_docs.py** (Main Script)
Powerful CLI with individual commands for each operation.

**Features:**
- Add, list, reindex, test, cleanup, status
- Full control over each step
- Choose specific strategies
- Detailed logging

**See:** `MANAGE_DOCS_GUIDE.md` for complete documentation

---

### 2. **quick_add_and_index.py** (All-in-One)
Single command that does everything automatically.

**Features:**
- Add documents
- Reindex automatically
- Test retrieval automatically
- Show final status
- Perfect for batch operations

**See examples below**

---

### 3. **add_and_index.bat** (Windows Only)
Simple batch script for Windows users.

**Double-click to run or:**
```cmd
add_and_index.bat "C:\path\to\docs" "sample query"
```

---

## 🚀 Quick Start

### Option A: Individual Steps (Most Control)

```bash
# 1. Add documents
python scripts/manage_docs.py add --source ./new_docs

# 2. Reindex
python scripts/manage_docs.py reindex --strategy all

# 3. Test
python scripts/manage_docs.py test --query "What's new?"

# 4. Check status
python scripts/manage_docs.py status
```

### Option B: All-in-One (Fastest)

```bash
# Everything in one command
python scripts/quick_add_and_index.py --source ./new_docs --test "What's new?"
```

### Option C: Windows Batch (Easiest)

```cmd
# Just run the batch file
add_and_index.bat "C:\path\to\docs" "sample query"
```

---

## 📖 Common Workflows

### Workflow 1: Add Single Document

```bash
python scripts/manage_docs.py add --file my_document.md
python scripts/manage_docs.py reindex --strategy all
python scripts/manage_docs.py list
```

### Workflow 2: Batch Add Multiple Docs

```bash
# Create folder with docs
mkdir new_docs
cp *.md new_docs/
cp *.pdf new_docs/

# Add and index
python scripts/quick_add_and_index.py --source ./new_docs --test "sample query"
```

### Workflow 3: Test Different Strategies

```bash
# Add documents
python scripts/manage_docs.py add --source ./docs

# Test with each strategy
python scripts/manage_docs.py reindex --strategy fixed_overlap
python scripts/manage_docs.py test --query "test" --strategy fixed_overlap

python scripts/manage_docs.py reindex --strategy structure_aware
python scripts/manage_docs.py test --query "test" --strategy structure_aware

python scripts/manage_docs.py reindex --strategy semantic
python scripts/manage_docs.py test --query "test" --strategy semantic

# Keep best one
python scripts/manage_docs.py reindex --strategy structure_aware
```

### Workflow 4: Continuous Ingestion

```bash
# Monitor a directory and re-index whenever files change
while True; do
    python scripts/quick_add_and_index.py --source ./incoming_docs --no-test
    sleep 3600  # Run every hour
done
```

---

## 🛠️ Detailed Command Reference

### manage_docs.py

#### Add Documents
```bash
# From directory
python scripts/manage_docs.py add --source /path/to/docs

# Specific files
python scripts/manage_docs.py add --file doc1.md doc2.pdf doc3.txt
```

#### List Documents
```bash
python scripts/manage_docs.py list
```

#### Reindex
```bash
# All strategies
python scripts/manage_docs.py reindex --strategy all

# Specific strategy
python scripts/manage_docs.py reindex --strategy structure_aware

# Without resetting
python scripts/manage_docs.py reindex --strategy all --no-reset
```

#### Test Retrieval
```bash
# Basic test
python scripts/manage_docs.py test --query "sample query"

# With options
python scripts/manage_docs.py test \
  --query "sample query" \
  --strategy semantic \
  --top-k 5
```

#### System Status
```bash
python scripts/manage_docs.py status
```

#### Cleanup
```bash
python scripts/manage_docs.py cleanup
```

---

### quick_add_and_index.py

```bash
# Add + reindex + test
python scripts/quick_add_and_index.py \
  --source ./new_docs \
  --test "sample query"

# Add + reindex + skip test
python scripts/quick_add_and_index.py \
  --source ./new_docs \
  --no-test

# Specific files
python scripts/quick_add_and_index.py \
  --file doc1.md doc2.pdf \
  --test "query"

# Custom strategy
python scripts/quick_add_and_index.py \
  --source ./docs \
  --strategy semantic \
  --test "query"
```

---

## 📊 What Gets Automated

### Adding Documents
- ✅ Validates file format
- ✅ Checks if file exists
- ✅ Copies to data/raw/
- ✅ Shows success/error
- ✅ Suggests next step

### Reindexing
- ✅ Loads all documents
- ✅ Chunks using chosen strategy
- ✅ Removes near-duplicates
- ✅ Resets ChromaDB collections
- ✅ Builds BM25 index
- ✅ Saves metadata
- ✅ Prints statistics

### Testing
- ✅ Runs retrieval
- ✅ Shows top results
- ✅ Displays scores
- ✅ No LLM cost (for speed)

---

## ⚙️ Setup

```bash
# Activate venv
.venv\Scripts\activate

# Install package
pip install -e .

# Check API key in .env
cat .env

# Verify setup
python scripts/manage_docs.py status
```

---

## 🔍 Supported Formats

✅ `.md` (Markdown)
✅ `.txt` (Plain text)
✅ `.pdf` (PDF)
✅ `.html` (HTML)
✅ `.htm` (HTML)

---

## 📁 File Structure

```
scripts/
├── manage_docs.py              Main CLI
├── quick_add_and_index.py      All-in-one
├── add_and_index.bat           Windows batch
├── MANAGE_DOCS_GUIDE.md        Full documentation
├── README.md                   This file
├── run_ingest.py               Legacy (still works)
└── run_eval.py                 Evaluation script
```

---

## 🔗 Integration

These scripts work with the full pipeline:

```
manage_docs.py
    ↓
Python ingestion module
    ↓
ChromaDB (semantic) + BM25 (keyword)
    ↓
RAG API (uvicorn)
    ↓
Dashboard (streamlit)
```

Start API after indexing:
```bash
uvicorn src.rag.main:app --host 0.0.0.0 --port 8000 --reload
```

Start dashboard:
```bash
streamlit run src/rag/dashboard.py
```

---

## 🆘 Troubleshooting

### "ModuleNotFoundError: No module named 'rag'"
```bash
.venv\Scripts\activate
pip install -e .
```

### "No documents found in data/raw/"
```bash
python scripts/manage_docs.py add --source ./your_docs
```

### "OPENAI_API_KEY is not set"
```bash
# Add to .env
echo "OPENAI_API_KEY=sk-..." >> .env
```

### "Could not reach API"
Start the API server:
```bash
uvicorn src.rag.main:app --host 0.0.0.0 --port 8000
```

---

## 📚 More Information

- **Full guide:** See `MANAGE_DOCS_GUIDE.md`
- **Pipeline overview:** See root `README.md`
- **Code examples:** See `manage_docs.py`

---

## ✨ Summary

**Before (Manual):**
```bash
# Copy files
# Run Python
# Wait for indexing
# Run tests
# Check results
# No easy way to manage
```

**Now (Automated):**
```bash
python scripts/quick_add_and_index.py --source ./docs --test "query"
# Done! ✅
```

---

**All document management is now automated!** 🚀
