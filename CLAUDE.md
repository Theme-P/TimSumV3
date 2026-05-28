# TimSum V3 — AI Meeting Transcription & Summarization

## Project Overview

Enterprise-grade Thai-English meeting transcription and summarization system.
Internal product for NTC / TimSum Project.

## Tech Stack

- **Frontend:** React 18 + Vite 6 + React Router 7 (no TypeScript, plain JSX)
- **Backend:** FastAPI + Celery + Redis (Python)
- **AI/ML:** WhisperX (large-v3) + PyAnnote + GPT-4.1 via NTC Gateway
- **Database:** MongoDB (pymongo, no ORM)
- **Storage:** MinIO (S3-compatible object storage)
- **Deploy:** Docker Compose (6 containers), NVIDIA GPU for worker

## Project Structure

```
TimSumV3/
├── frontend/
│   └── src/
│       ├── App.jsx              # Root component + routing
│       ├── main.jsx             # Entry point
│       ├── components/          # Reusable components
│       │   └── admin/           # Admin dashboard components
│       ├── contexts/            # React contexts
│       ├── pages/               # Page-level components
│       └── styles/              # CSS files
├── backend/
│   ├── api.py                   # FastAPI app entrypoint
│   ├── main.py                  # CLI/script entrypoint
│   ├── app/
│   │   ├── core/                # Config, auth utilities
│   │   │   ├── config.py
│   │   │   └── auth.py
│   │   ├── routers/             # API route handlers
│   │   │   ├── auth.py
│   │   │   ├── admin.py
│   │   │   ├── user.py
│   │   │   ├── package.py
│   │   │   ├── quota.py
│   │   │   ├── consent.py
│   │   │   ├── activity.py
│   │   │   ├── queue.py
│   │   │   ├── system_admin.py
│   │   │   └── voice_samples.py
│   │   ├── services/            # Business logic
│   │   │   ├── mongo.py         # MongoDB connection & queries
│   │   │   ├── pipeline.py      # Audio processing pipeline
│   │   │   ├── summarizer.py    # GPT-4.1 summarization
│   │   │   ├── storage.py       # MinIO operations
│   │   │   ├── email_service.py
│   │   │   ├── text_cleaner.py
│   │   │   └── voice_matching.py
│   │   ├── tasks/               # Celery async tasks
│   │   └── models/              # Data models
│   ├── scripts/                 # Admin scripts (create_admin, init_mongo)
│   └── tests/
├── docker-compose.yml           # Production compose
├── docker-compose.override.yml  # Dev overrides (volume mounts, hot reload)
└── .env                         # Secrets (DO NOT COMMIT)
```

## Development

### Running Locally (Docker)

```bash
# Start all services (dev mode with hot reload)
sudo docker compose up -d

# Production mode (no override)
sudo docker compose -f docker-compose.yml up -d

# Restart worker after code changes
sudo docker compose restart worker
```

### Services & Ports

| Service | Port | Notes |
|---------|------|-------|
| Frontend (Vite dev) | 3000 | Proxies `/api/*` to backend |
| Backend (FastAPI) | 8000 | API docs at `/docs` |
| MinIO Console | 9001 | Object storage UI |
| MongoDB | internal | No exposed port |
| Redis | internal | No exposed port |

### Frontend Dev

- Vite dev server with proxy: `/api/*` → `http://localhost:8000`
- No TypeScript — all `.jsx` files
- No CSS framework — plain CSS in `src/styles/`
- No state management library — React Context only

### Backend Dev

- FastAPI app is in `api.py` (root), routers in `app/routers/`
- Celery worker runs GPU tasks (transcription) — separate container
- MongoDB accessed via pymongo directly (no ODM)
- All env vars loaded from `.env` via `app/core/config.py`

## Conventions

- UI supports Thai language — error messages, labels, and content are in Thai
- 3-level role system: `superadmin` > `admin` > `user`
- API routes prefixed with `/api/`
- Auth via JWT tokens (stored client-side)
- Async jobs use Celery task IDs for polling
- File uploads go to MinIO, metadata to MongoDB

## Important Notes

- Worker container requires NVIDIA GPU runtime
- Never expose MongoDB or Redis ports externally
- `.env` contains all secrets — never commit it
- WhisperX model cache is in a Docker volume (`whisperx_cache`)
