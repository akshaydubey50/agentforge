# Docker Setup Guide

## ✅ Prerequisites

1. **Docker Desktop installed** - Download from https://www.docker.com/products/docker-desktop
2. **.env file with API key**:
   ```bash
   cat .env
   # Should contain:
   # OPENAI_API_KEY=sk-...
   ```
3. **Documents in data/raw/** (already there ✅)

---

## 🚀 Quick Start (3 Commands)

### **Option 1: Build & Run Everything**

```bash
cd D:\Akshay\python-projects\rag-production-pipeline

# Build images
docker-compose build

# Start all services
docker-compose up
```

**Wait for output:**
```
rag-seed   | ... "indexed_chunks": 161 ...
rag-api    | Uvicorn running on http://0.0.0.0:8000
rag-dashboard | You can now view your Streamlit app in your browser
```

Then open:
- **API:** http://localhost:8000
- **Dashboard:** http://localhost:8501
- **Health:** http://localhost:8000/health

---

### **Option 2: Faster (Skip Seed, Use Existing Data)**

If you already have indexes built:

```bash
docker-compose up api dashboard
```

This skips the seed service (faster startup).

---

## 📊 Services Running

| Service | Port | Purpose |
|---------|------|---------|
| **api** | 8000 | FastAPI backend |
| **dashboard** | 8501 | Streamlit upload UI |
| **seed** | - | (startup only) Build indexes |

---

## 🎯 What to Do

### **1. Upload Documents (in Dashboard)**

1. Open http://localhost:8501
2. Select "Upload Documents" mode
3. Upload new files
4. Click "Reindex Now"
5. Documents appear

### **2. Query Documents (in Dashboard)**

1. Select "Query Documents" mode
2. Type question
3. Click "Search"
4. View answer with sources

### **3. Check API Status**

```bash
curl http://localhost:8000/health
# Should return: {"status":"ok"}
```

### **4. List Documents (via API)**

```bash
curl http://localhost:8000/v1/documents
# Shows all indexed documents
```

---

## 🛑 Stop Services

```bash
# Stop but keep volumes (data)
docker-compose down

# Stop and remove everything (data stays)
docker-compose down --rmi local

# Stop and remove EVERYTHING including data
docker-compose down -v
```

---

## 🔍 Common Commands

```bash
# View logs
docker-compose logs -f

# View specific service logs
docker-compose logs -f api
docker-compose logs -f dashboard
docker-compose logs -f seed

# Rebuild images (after code changes)
docker-compose build --no-cache

# Run command in container
docker-compose exec api python scripts/manage_docs.py list

# Remove images
docker-compose down --rmi all

# Full restart
docker-compose restart
```

---

## 📁 File Structure

```
project/
├── data/
│   ├── raw/              ← Your documents (uploads go here)
│   ├── chroma/           ← Dense indexes (created by seed)
│   └── processed/        ← Sparse indexes (created by seed)
│
├── src/rag/
│   ├── simple_upload.py  ← Dashboard UI
│   ├── main.py           ← API
│   └── ...
│
├── docker-compose.yml    ← Services config
├── Dockerfile            ← Image definition
└── .env                  ← API keys
```

---

## ⚠️ Common Issues & Fixes

### **Issue: "Cannot connect to Docker daemon"**

```bash
# Make sure Docker Desktop is running
# Mac/Windows: Start Docker Desktop app
# Linux: sudo systemctl start docker
```

### **Issue: "Port 8000 already in use"**

```bash
# Kill the service using port 8000
# Windows
netstat -ano | findstr :8000
taskkill /PID <PID> /F

# Or use different port
docker-compose up --renumber-ports
```

### **Issue: "Files not updating after upload"**

```bash
# Stop and restart
docker-compose down
docker-compose up

# Or clear cache
docker-compose down -v
docker-compose up --build
```

### **Issue: "Seed service fails"**

```bash
# View seed logs
docker-compose logs seed

# Run seed manually
docker-compose run seed python scripts/run_ingest.py --strategy all
```

### **Issue: "API returns 404"**

```bash
# Check API is running
docker-compose ps

# Check health
curl http://localhost:8000/health

# View logs
docker-compose logs api
```

---

## 🐳 Docker Benefits vs Local

| Aspect | Local | Docker |
|--------|-------|--------|
| Setup Time | 10 min | 5 min |
| Dependencies | Manual | Automatic |
| Reproducibility | OS-dependent | Consistent |
| Deployment | Complex | Simple |
| Performance | Fastest | Slight overhead |
| Isolation | No | Full |
| Cleanup | Manual | One command |

---

## 📋 Workflow with Docker

### **Development Workflow**

```bash
# 1. Start services
docker-compose up

# 2. Edit code locally (in your editor)

# 3. Dashboard auto-reloads
# API needs restart for code changes:
docker-compose restart api

# 4. Done
```

### **Production Deployment**

```bash
# On server
git clone <repo>
cd rag-production-pipeline

# Build and run
docker-compose build
docker-compose up -d  # Detached mode

# Check status
docker-compose ps
docker-compose logs -f
```

---

## 🔄 Reindexing with Docker

### **Option 1: Full Restart (Reindex)**

```bash
docker-compose down -v  # Remove all data
docker-compose up       # Rebuild indexes
```

### **Option 2: Keep Data, Just Reindex**

```bash
# Run seed service
docker-compose run seed python scripts/run_ingest.py --strategy all

# Or via exec
docker-compose exec api python scripts/manage_docs.py reindex --strategy all
```

---

## 📊 Resource Usage

Monitor Docker resources:

```bash
# View stats
docker stats

# View disk usage
docker system df
```

**Typical Usage:**
- Memory: 500MB - 1GB
- Disk: 100MB - 500MB (depends on data size)
- CPU: Minimal when idle

---

## 🚀 Advanced: Custom Configuration

### **Run specific services only**

```bash
# Just API
docker-compose up api

# Just Dashboard
docker-compose up dashboard

# API + Dashboard (skip seed)
docker-compose up api dashboard
```

### **Override environment**

```bash
# Use different API URL
RAG_API_URL=http://custom-api:8000 docker-compose up dashboard
```

### **Run as background service**

```bash
# Start in background
docker-compose up -d

# View logs
docker-compose logs -f

# Stop background service
docker-compose stop
```

---

## ✅ Verification Checklist

After running `docker-compose up`:

- [ ] API starts (check logs for "Uvicorn running")
- [ ] Seed completes (check for "indexed_chunks")
- [ ] Dashboard starts (Streamlit output)
- [ ] API responds: `curl http://localhost:8000/health`
- [ ] Dashboard loads: http://localhost:8501
- [ ] Can upload files
- [ ] Can query documents

---

## 📚 Summary

**Instead of 3 terminals, Docker runs everything in containers:**

```bash
# All in one command
docker-compose up

# That's it! All services running:
# - Seed (indexes data)
# - API (backend)
# - Dashboard (UI)
```

**Benefits:**
- No environment setup
- Reproducible everywhere
- Easy to deploy
- One command to start/stop

---

**Ready? Let's go!**

```bash
cd D:\Akshay\python-projects\rag-production-pipeline
docker-compose up
```

Then visit http://localhost:8501 🚀
