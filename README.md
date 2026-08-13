# YMSLI Template Hub

AI-powered template hub (PS-08): semantic search over versioned templates, conversational context gathering, and auto-filled Word / Excel / PowerPoint documents.

## Features

- **Chat-first portal** — natural language search & document creation
- **Semantic template search** — ChromaDB vector retrieval
- **Version history** — approved latest version per template
- **Conversational agent** — asks 3–4 context questions, then auto-fills
- **Outputs** — `.docx` / `.xlsx` / `.pptx`
- **Upload → parse → auto-template** — Gemini extracts fields and picks the best template if you don't specify one
- **OneDrive (Microsoft Graph)** — fetch files, check read/write ACL, GitHub-style commits + restore
- **Access control** — role-based template permissions (demo users)
- **Usage analytics** — most-used & stale templates

## Architecture

```
React (Vite)  →  FastAPI Agent API  →  ChromaDB + SQLite metadata
                              ↓
                    Bedrock / Mock LLM
                              ↓
              python-docx / openpyxl / python-pptx
```

## Quick start

### Backend

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
python -m app.bootstrap   # seed templates + index Chroma
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

Demo users (password: `demo`):
- `consultant` — read/write most templates
- `approver` — authorization on gated templates
- `joiner` — read-only onboarding set

OneDrive panel (right side):
- Default `GRAPH_MODE=mock` — click **Connect mock drive**
- For real OneDrive, see [ONEDRIVE.md](./ONEDRIVE.md)

## Chat examples

- `Find the latest MOM template`
- `Create a meeting minutes document`
- `I need a project plan for Project Orion`
- `Generate an API specification`
- `Show version history for QMM proposal`

## Environment

Copy `backend/.env.example` → `backend/.env`

| Variable | Default | Notes |
|----------|---------|-------|
| `LLM_PROVIDER` | `gemini` | `gemini` \| `mock` \| `bedrock` \| `openai` |
| `GEMINI_API_KEY` | — | Required for live Gemini |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model id |
| `GRAPH_MODE` | `mock` | `mock` \| `live` OneDrive |
| `AZURE_CLIENT_ID` | — | Required for live Graph |
| `AWS_REGION` | `ap-south-1` | Bedrock region |
| `BEDROCK_MODEL_ID` | `anthropic.claude-3-haiku...` | When using Bedrock |
| `OPENAI_API_KEY` | — | Optional OpenAI |

## AWS mapping (enterprise)

| Local | AWS |
|-------|-----|
| ChromaDB | Amazon OpenSearch / Kendra |
| `./storage` | S3 |
| Mock LLM | Amazon Bedrock |
| SQLite | RDS PostgreSQL |
| Local files | SharePoint sync (good-to-have) |

## Project layout

```
backend/app/     API, agent, RAG, doc generation
backend/data/    Seed template definitions + versions
frontend/src/    React chat portal
storage/         Generated documents
```
