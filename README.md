# TalentOS — Recruitment Intelligence Platform

Full-stack recruitment management with PostgreSQL persistence and AI-powered CV matching.

## Quick Start (1 command)

```bash
# 1. Clone / unzip the project
cd talentos

# 2. Create your .env file
cp .env.example .env
# Edit .env — set your ANTHROPIC_API_KEY

# 3. Launch everything
docker compose up --build

# App is live at → http://localhost:3000
```

That's it. Docker Compose starts PostgreSQL, the FastAPI backend, and Nginx automatically.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    docker compose                        │
│                                                         │
│  ┌─────────┐    ┌──────────────┐    ┌────────────────┐  │
│  │  nginx  │───▶│   backend    │───▶│   PostgreSQL   │  │
│  │  :3000  │    │  FastAPI     │    │   postgres:16  │  │
│  │  (80)   │    │  :8000       │    │   talentos db  │  │
│  └─────────┘    └──────────────┘    └────────────────┘  │
│       │                │                                 │
│  static SPA      Anthropic API                          │
│  (Vue 3)         (Claude claude-opus-4-5)                    │
└─────────────────────────────────────────────────────────┘
```

| Service   | Image                  | Role                              |
|-----------|------------------------|-----------------------------------|
| `nginx`   | nginx:1.27-alpine      | Reverse proxy + static files      |
| `backend` | python:3.12-slim       | FastAPI + SQLAlchemy + Claude AI  |
| `db`      | postgres:16-alpine     | Persistent data store             |

## Services & Ports

| Service  | Internal port | External (default) |
|----------|---------------|--------------------|
| nginx    | 80            | **3000**           |
| backend  | 8000          | not exposed        |
| postgres | 5432          | not exposed        |

Change the port in `.env`: `APP_PORT=8080`

---

## Environment Variables

| Variable            | Required | Default           | Description            |
|---------------------|----------|-------------------|------------------------|
| `ANTHROPIC_API_KEY` | ✅ Yes   | —                 | Your Claude API key    |
| `POSTGRES_PASSWORD` | No       | `talentos_secret` | PostgreSQL password    |
| `APP_PORT`          | No       | `3000`            | Host port for the app  |

---

## Database Schema

### `job_descriptions`
| Column            | Type        | Notes               |
|-------------------|-------------|---------------------|
| id                | UUID (PK)   | auto-generated      |
| title             | VARCHAR     | indexed             |
| department        | VARCHAR     |                     |
| location          | VARCHAR     |                     |
| type              | VARCHAR     | Full-time, etc.     |
| experience        | VARCHAR     |                     |
| salary            | VARCHAR     |                     |
| status            | VARCHAR     | indexed             |
| summary           | TEXT        |                     |
| responsibilities  | TEXT        |                     |
| requirements      | TEXT        |                     |
| nice_to_have      | TEXT        |                     |
| benefits          | TEXT        |                     |
| skills            | JSON        | array of strings    |
| created_at        | TIMESTAMPTZ |                     |
| updated_at        | TIMESTAMPTZ | auto-updated        |

### `cv_analyses`
| Column          | Type        | Notes                      |
|-----------------|-------------|----------------------------|
| id              | UUID (PK)   |                            |
| jd_id           | UUID (FK)   | → job_descriptions.id      |
| jd_title        | VARCHAR     | denormalised for speed     |
| filename        | VARCHAR     | original CV filename       |
| result          | JSON        | full Claude analysis       |
| score           | INTEGER     | 0–100, for stats queries   |
| candidate_name  | VARCHAR     | extracted from CV          |
| recommendation  | VARCHAR     | strong_yes/yes/maybe/no    |
| created_at      | TIMESTAMPTZ |                            |

---

## API Reference

| Method | Path                  | Description                     |
|--------|-----------------------|---------------------------------|
| GET    | `/api/jds`            | List JDs (optional ?status=)    |
| GET    | `/api/jds/{id}`       | Get single JD                   |
| POST   | `/api/jds`            | Create JD                       |
| PUT    | `/api/jds/{id}`       | Update JD                       |
| DELETE | `/api/jds/{id}`       | Delete JD                       |
| POST   | `/api/analyze`        | Upload CV + analyse match       |
| POST   | `/api/chat`           | Streaming SSE chat about JD     |
| GET    | `/api/stats`          | Dashboard statistics            |
| GET    | `/api/analyses`       | Analysis history                |
| GET    | `/health`             | Health check                    |
| GET    | `/api/docs`           | Swagger UI (dev)                |

---

## Useful Commands

```bash
# Start in background
docker compose up -d --build

# View logs
docker compose logs -f
docker compose logs -f backend

# Connect to PostgreSQL
docker compose exec db psql -U talentos -d talentos

# Stop everything
docker compose down

# Stop + wipe all data (fresh start)
docker compose down -v
```

---

## CV Parsing Support

| Format | Parser     | Notes                              |
|--------|------------|------------------------------------|
| .pdf   | PyMuPDF    | Text-based PDFs; scanned = fallback|
| .docx  | python-docx| Full paragraph extraction          |
| .txt   | UTF-8      | Direct decode                      |

All uploaded CVs are stored in the `uploads_data` Docker volume.
