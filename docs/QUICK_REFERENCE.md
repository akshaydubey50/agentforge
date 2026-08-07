# Quick Reference Card

## 🚀 Start Here

### For Beginners
```bash
# Open upload UI (easiest)
streamlit run src/rag/upload_dashboard.py
# Then drag & drop documents in browser
```

### For Power Users
```bash
# Single command: add + reindex + test
python scripts/quick_add_and_index.py --source ./docs --test "query"
```

### For Bulk Operations
```bash
# Handle 1000+ documents
python scripts/bulk_ingest.py --source ./bulk_docs
```

---

## 📋 Commands Cheat Sheet

### Add Documents
```bash
python scripts/manage_docs.py add --source ./docs        # From directory
python scripts/manage_docs.py add --file doc.md          # Single file
python scripts/manage_docs.py add --file doc1.md doc2.pdf # Multiple
```

### Reindex
```bash
python scripts/manage_docs.py reindex --strategy all              # All strategies
python scripts/manage_docs.py reindex --strategy structure_aware  # One strategy
```

### Query & Test
```bash
python scripts/manage_docs.py test --query "sample query"
python scripts/manage_docs.py list
python scripts/manage_docs.py status
```

### Bulk Operations
```bash
python scripts/bulk_ingest.py --source ./docs
python scripts/bulk_ingest.py --source ./docs --report report.json
python scripts/bulk_ingest.py --source ./docs --batch-size 200
```

### All-in-One
```bash
python scripts/quick_add_and_index.py --source ./docs --test "query"
```

---

## 🌐 Web UI URLs

| Interface | URL | Purpose |
|-----------|-----|---------|
| Upload UI | `http://localhost:8501` | Add docs, Query, Manage |
| Query UI | `http://localhost:8501` | Original query interface |
| API | `http://localhost:8000` | Backend API |
| Health | `http://localhost:8000/health` | Check API status |

---

## 📊 Task Matrix

| Task | Command | Time |
|------|---------|------|
| Add 1 doc | `manage_docs.py add --file` | < 1 min |
| Add 10 docs | `quick_add_and_index.py --source` | 2-3 min |
| Add 100 docs | `manage_docs.py add --source` | 5-10 min |
| Add 1000+ docs | `bulk_ingest.py --source` | 15-60 min |
| Reindex all | `manage_docs.py reindex --strategy all` | 2-30 min |
| Test query | `manage_docs.py test --query` | 1-5 sec |
| List docs | `manage_docs.py list` | < 1 sec |

---

## 🛠️ Setup (One-Time)

```bash
# 1. Activate virtual environment
.venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Add API key to .env
echo "OPENAI_API_KEY=sk-..." >> .env

# 4. Verify setup
python scripts/manage_docs.py status
```

---

## ▶️ Running Services

### Terminal 1: API Server
```bash
uvicorn src.rag.main:app --host 0.0.0.0 --port 8000 --reload
# API available at http://localhost:8000
```

### Terminal 2: Upload UI
```bash
streamlit run src/rag/upload_dashboard.py
# UI available at http://localhost:8501
```

### Terminal 3: CLI Commands
```bash
# Run any commands here
python scripts/manage_docs.py status
python scripts/bulk_ingest.py --source ./docs
```

---

## 📁 File Structure

```
data/
├── raw/          ← Put your documents here
├── chroma/       ← Dense search indexes (auto-created)
└── processed/    ← Sparse search indexes (auto-created)

scripts/
├── manage_docs.py              Main CLI
├── quick_add_and_index.py      All-in-one
├── bulk_ingest.py              For large datasets
└── MANAGE_DOCS_GUIDE.md        Full documentation
```

---

## 🎯 Common Workflows

### Workflow 1: Single Doc Upload
```bash
# Via CLI
python scripts/manage_docs.py add --file my_doc.md
python scripts/manage_docs.py reindex --strategy all

# Via UI
1. Open http://localhost:8501
2. Drag & drop file
3. Click Reindex
```

### Workflow 2: Bulk Upload
```bash
# Via CLI (recommended)
python scripts/bulk_ingest.py --source ./new_docs

# Via UI
1. Open http://localhost:8501
2. Select "Bulk Operations"
3. Upload all files
4. Click Bulk Reindex
```

### Workflow 3: Query Documents
```bash
# Via UI
1. Open http://localhost:8501
2. Select "Query Documents"
3. Type question
4. Click Search

# Via API
curl -X POST http://localhost:8000/v1/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What is machine learning?"}'
```

---

## ⚡ Quick Answers

**Q: How do I add documents?**
A: `python scripts/manage_docs.py add --source ./docs`

**Q: How do I add 1000+ documents?**
A: `python scripts/bulk_ingest.py --source ./huge_corpus`

**Q: Which strategy is fastest?**
A: `fixed_overlap` (but `structure_aware` is better quality)

**Q: Which strategy is best quality?**
A: `semantic` (but slowest and most expensive)

**Q: Can I upload via browser?**
A: Yes! `streamlit run src/rag/upload_dashboard.py`

**Q: How do I test if indexing worked?**
A: `python scripts/manage_docs.py test --query "sample query"`

**Q: How do I automate this?**
A: Use cron: `0 2 * * * python scripts/bulk_ingest.py --source /data`

**Q: Which mode should I use?**
A: Beginners → UI, Power users → CLI, Production → Both

---

## 📈 Performance Tips

- Small corpus (< 100 docs): `semantic` strategy
- Medium corpus (100-1000): `structure_aware` strategy  
- Large corpus (1000+): `fixed_overlap` strategy
- Very large corpus (10000+): Use `--batch-size 300`
- Memory issues: Reduce batch size or use faster strategy

---

## 🆘 Common Issues

| Error | Solution |
|-------|----------|
| "ModuleNotFoundError" | `.venv\Scripts\activate` then `pip install -e .` |
| "OPENAI_API_KEY not set" | Add to `.env`: `echo "OPENAI_API_KEY=sk-..." >> .env` |
| "No documents found" | Add docs: `python scripts/manage_docs.py add --source ./docs` |
| "Out of memory" | Reduce batch: `--batch-size 50` or use faster strategy |
| "API not responding" | Start API: `uvicorn src.rag.main:app --host 0.0.0.0 --port 8000` |

---

## 📚 Full Documentation

- **scripts/README.md** - All scripts overview
- **scripts/MANAGE_DOCS_GUIDE.md** - Detailed CLI docs
- **scripts/BULK_AND_UPLOAD_GUIDE.md** - Bulk & upload guide
- **AUTOMATION_SUMMARY.md** - Complete automation guide

---

## ✨ One-Liners

```bash
# Add + Reindex + Test
python scripts/quick_add_and_index.py --source ./docs --test "query"

# List all documents
python scripts/manage_docs.py list

# Check system status
python scripts/manage_docs.py status

# Test retrieval
python scripts/manage_docs.py test --query "sample"

# Bulk ingest with report
python scripts/bulk_ingest.py --source ./docs --report out.json

# Windows batch
add_and_index.bat "C:\docs" "query"
```

---

## 🎯 Choose Your Path

```
Want to add documents?
  ├─ Easily (1-2 docs) → Use UI
  ├─ Quickly (10 docs) → Use quick_add_and_index.py
  ├─ Bulk (100+ docs) → Use bulk_ingest.py
  └─ Automate → Use cron + manage_docs.py

Want to query documents?
  ├─ Via web → Use upload_dashboard.py
  ├─ Via API → Use curl/Python requests
  └─ Via code → Import from rag.retrieval

Want to manage corpus?
  ├─ Via web UI → streamlit run upload_dashboard.py
  └─ Via CLI → python scripts/manage_docs.py
```

---

## 🚀 Ready? Start Here!

### Absolute Beginner
```bash
streamlit run src/rag/upload_dashboard.py
# Then just drag & drop files!
```

### Quick Power User
```bash
python scripts/quick_add_and_index.py --source ./docs --test "query"
```

### Production
```bash
python scripts/bulk_ingest.py --source /data/incoming --report metrics.json
# Schedule with: 0 2 * * * /path/to/above/command
```

---

**Everything is automated! No more manual steps!** ✨
