# App Component Obsolescence Maintenance

A full-stack application for querying Bill of Materials (BOM) documents using FAISS vector embeddings, multi-agent processing, OCR extraction, and an LLM backend powered by Ollama.

- **Backend** — Python 3.11 / FastAPI + FAISS + PaddleOCR
- **Frontend** — React 19 / Material UI
- **LLM** — Ollama (runs on the host, not in Docker)

---

## Repository Layout

```
.
├── backend/                  # FastAPI application
│   ├── app/                  # Application source code
│   ├── configs/              # YAML config and known manufacturers
│   ├── uploads/              # Uploaded BOM PDFs (runtime, git-ignored)
│   ├── ocr_outputs/          # OCR extracted text (runtime, git-ignored)
│   ├── index-faiss-store/    # FAISS vector index (runtime, git-ignored)
│   ├── documents/            # Processed documents (runtime, git-ignored)
│   ├── requirements.txt
│   ├── Dockerfile.dev
│   ├── Dockerfile.dev.frontend
│   ├── Dockerfile.prod
│   ├── docker-compose.dev.yml
│   └── docker-compose.prod.yml
└── frontend/                 # React application
    ├── src/
    ├── public/
    └── package.json
```

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.11+ | For local (non-Docker) backend |
| Node.js | 20+ | For local (non-Docker) frontend |
| Docker & Docker Compose | Latest | For Docker-based setup |
| Ollama | Latest | Must run on the host at port `11434` |

### Install & start Ollama

Download from https://ollama.com, then start the Ollama service:

```bash
ollama serve        # starts Ollama on localhost:11434
```

---

## Required Ollama Models

The backend uses three Ollama models. Pull all of them before starting the application:

```bash
# 1. Query intent parser — lightweight 3B model, fast inference
ollama pull llama3.2:3b

# 2. Response reviewer — reasoning/thinking model for result filtering
ollama pull gpt-oss:latest

# 3. Vector embeddings — used by FAISS to index and search BOM parts
ollama pull nomic-embed-text
```

| Model | Tag | Role | Used in |
|-------|-----|------|---------|
| `llama3.2` | `3b` | **Query Intent Agent** — parses natural language queries into structured filters (limit, manufacturer, part numbers) | `app/multi_agent_faiss.py` |
| `gpt-oss` | `latest` | **Response Reviewer Agent** — thinking model that validates and filters the result set against the original query before returning to the user | `app/multi_agent_faiss.py` |
| `nomic-embed-text` | `latest` | **FAISS Embeddings** — generates 768-dimension text embeddings used to index BOM parts and perform semantic similarity search | `app/faiss_bom_store.py` |

> **PaddleOCR** (used as an OCR fallback for image-based PDFs) is a Python package, not an Ollama model. Its model weights (~150 MB) are downloaded automatically from the PaddlePaddle model hub on the **first** document upload. This one-time download happens at server startup to avoid freezing during a real upload — expect a delay on the first run.

### Verify models are available

```bash
ollama list
# Expected output (tags may vary):
# NAME                    ID              SIZE
# llama3.2:3b             ...             ~2 GB
# gpt-oss:latest          ...             varies
# nomic-embed-text:latest ...             ~274 MB
```

---

## Environment Variables

Create a `backend/.env` file before running (copy the template below):

```env
OLLAMA_BASE_URL=http://localhost:11434
KMP_DUPLICATE_LIB_OK=TRUE
```

> In Docker, `OLLAMA_BASE_URL` is automatically set to `http://host.docker.internal:11434` via the compose files — you only need the `.env` for local runs.

---

## Development Setup

### Option A — Docker (recommended)

Both backend and frontend run in containers with **live reload**. Any file save is instantly reflected without rebuilding the image.

```bash
# From the backend/ directory
cd backend

# First run (builds images)
docker-compose -f docker-compose.dev.yml up --build

# Subsequent runs
docker-compose -f docker-compose.dev.yml up

# Stop
docker-compose -f docker-compose.dev.yml down
```

| Service | URL |
|---------|-----|
| Frontend (React dev server) | http://localhost:3000 |
| Backend (FastAPI) | http://localhost:8000 |
| Swagger / API docs | http://localhost:8000/docs |

**How live reload works:**
- Backend — `uvicorn --reload` watches the bind-mounted `backend/app/` directory; saving any `.py` file restarts the server.
- Frontend — Create React App HMR watches bind-mounted `frontend/src/` and `frontend/public/`; saving any `.js`/`.css` file hot-reloads the browser tab.

---

### Option B — Local (without Docker)

Run the backend and frontend directly on your machine.

#### 1. Backend

```bash
cd backend

# Create and activate virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start the dev server
uvicorn app.main_faiss:app --host 0.0.0.0 --port 8000 --reload
```

Backend available at http://localhost:8000  
Swagger UI at http://localhost:8000/docs

#### 2. Frontend

Open a second terminal:

```bash
cd frontend

npm install
npm start
```

Frontend available at http://localhost:3000

> The frontend is pre-configured to call the backend at `http://localhost:8000` via the `REACT_APP_API_URL` environment variable.

---

## Production Setup

In production, React is built at image-build time and served as static files by the FastAPI server — a **single container** handles both.

### Option A — Docker (recommended)

```bash
# From the backend/ directory
cd backend

# Build and start (detached)
docker-compose -f docker-compose.prod.yml up -d --build

# View logs
docker-compose -f docker-compose.prod.yml logs -f

# Stop
docker-compose -f docker-compose.prod.yml down
```

| Service | URL |
|---------|-----|
| Application (frontend + API) | http://localhost:8000 |
| Swagger / API docs | http://localhost:8000/docs |

**Persistent data** is stored in named Docker volumes so uploads, OCR outputs, and the FAISS index survive container restarts and rebuilds:

| Volume | Contents |
|--------|----------|
| `uploads_data` | Uploaded BOM PDF files |
| `ocr_data` | OCR extracted text and status |
| `faiss_data` | FAISS vector index and metadata |

To deploy an updated frontend or backend, simply rebuild:

```bash
docker-compose -f docker-compose.prod.yml up -d --build
```

---

### Option B — Local production build

#### 1. Build the frontend

```bash
cd frontend
npm install
npm run build
# Outputs to frontend/build/
```

#### 2. Run the backend

```bash
cd backend
source .venv/bin/activate   # or .venv\Scripts\activate on Windows

# Set environment variable so FastAPI serves the React build
# (the build/ directory is auto-detected by main_faiss.py)

uvicorn app.main_faiss:app --host 0.0.0.0 --port 8000 --workers 2
```

The FastAPI server automatically detects `frontend/build/` and serves the React app at `http://localhost:8000`.

---

## Useful Commands

### Docker

```bash
# Rebuild only the backend image
docker-compose -f docker-compose.dev.yml build backend

# Open a shell inside the running backend container
docker exec -it ai-cs-backend-dev bash

# Check container health
docker ps

# Remove all named production volumes (WARNING: deletes all data)
docker-compose -f docker-compose.prod.yml down -v
```

### Backend tests

```bash
cd backend
source .venv/bin/activate

# Run all tests
python -m pytest tests/

# Run a specific test file
python -m pytest tests/test_faiss_multi_agent.py -v
```

### Frontend tests

```bash
cd frontend
npm test
```

---

## API Overview

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Swagger UI |
| `POST` | `/upload` | Upload a BOM PDF |
| `POST` | `/query` | Query the indexed BOM data |
| `POST` | `/reindex` | Re-build the FAISS index |
| `GET` | `/` | Serves the React frontend |

---

## Troubleshooting

**Ollama not reachable inside Docker**

Docker containers reach the host via `host.docker.internal`. Make sure Ollama is running (`ollama serve`) before starting the containers. On Linux, the `extra_hosts: host.docker.internal:host-gateway` entry in the compose file handles this automatically.

**`KMP_DUPLICATE_LIB_OK` error on Windows**

Set the environment variable before starting the backend:
```powershell
$env:KMP_DUPLICATE_LIB_OK = "TRUE"
uvicorn app.main_faiss:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend HMR not working in Docker on Windows**

The compose file already sets `CHOKIDAR_USEPOLLING=true` and `WATCHPACK_POLLING=true` to handle this. If changes still aren't detected, try restarting the frontend container:
```bash
docker-compose -f docker-compose.dev.yml restart frontend
```

**Port already in use**

```bash
# Find what is using port 8000
netstat -ano | findstr :8000    # Windows
lsof -i :8000                   # macOS / Linux
```
