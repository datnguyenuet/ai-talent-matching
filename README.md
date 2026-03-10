# TalentOS v3 — Authentication + Role-Based Access

## Quick Start

```bash
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY
docker compose up --build
# → http://localhost:3000
```

## Default Accounts

| Email               | Password | Role  |
|---------------------|----------|-------|
| admin@talentos.ai   | 123456   | Admin |

New sign-ups are assigned the **user** role automatically.

---

## Role Permissions

| Feature               | Admin | User          |
|-----------------------|-------|---------------|
| View JDs              | ✅    | ✅            |
| Preview JD            | ✅    | ✅            |
| Create / Edit JD      | ✅    | ❌ (API 403)  |
| Delete JD             | ✅    | ❌ (API 403)  |
| Upload & analyse CV   | ✅    | ✅ (own only) |
| View analyses         | ✅ All| ✅ Own only   |
| AI Chat about JD      | ✅    | ✅            |
| View user list        | ✅    | ❌            |
| Dashboard stats       | ✅ All| ✅ Own scope  |

Permissions are enforced **both in the UI and on the API** — protected endpoints return `403 Forbidden` for non-admins.

---

## Authentication

### Local (email + password)
- `POST /api/auth/signup` — creates a user account
- `POST /api/auth/signin` — returns a JWT token
- JWT stored in `localStorage`, sent as `Authorization: Bearer <token>`

### Google OAuth
1. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` in `.env`
2. Add `http://localhost:3000/auth/google/callback` as an authorised redirect URI in Google Cloud Console
3. The **Sign In with Google** button appears automatically

### Token Expiry
Tokens expire after 24 hours (configurable via `ACCESS_TOKEN_EXPIRE_MINUTES`).

---

## Architecture

```
Client (Vue 3 SPA)
  ↕ JWT in Authorization header
Nginx :3000
  ├── /api/* → FastAPI :8000
  └── /* → static index.html

FastAPI
  ├── /api/auth/*       — public
  ├── /api/jds (GET)    — any authenticated user
  ├── /api/jds (write)  — admin only
  ├── /api/analyze      — any user (scoped to their user_id)
  ├── /api/analyses     — admin sees all; user sees own
  ├── /api/chat         — any authenticated user
  ├── /api/stats        — scoped by role
  └── /api/admin/*      — admin only

PostgreSQL
  ├── users
  ├── job_descriptions
  └── cv_analyses (user_id FK → users)
```

---

## Google OAuth Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create / select a project → **APIs & Services → Credentials**
3. Create **OAuth 2.0 Client ID** (Web application)
4. Add authorised redirect URI: `http://localhost:3000/auth/google/callback`
   - For production: `https://yourdomain.com/auth/google/callback`
5. Copy Client ID and Secret into `.env`

---

## API Reference

### Auth
| Method | Path                        | Auth     | Description           |
|--------|-----------------------------|----------|-----------------------|
| POST   | `/api/auth/signup`          | None     | Register              |
| POST   | `/api/auth/signin`          | None     | Login → JWT           |
| GET    | `/api/auth/me`              | Any      | Current user info     |
| GET    | `/api/auth/google`          | None     | Get Google OAuth URL  |
| GET    | `/api/auth/google/callback` | None     | OAuth callback        |
| GET    | `/api/admin/users`          | Admin    | List all users        |

### JDs
| Method | Path            | Auth  | Description   |
|--------|-----------------|-------|---------------|
| GET    | `/api/jds`      | Any   | List JDs      |
| POST   | `/api/jds`      | Admin | Create JD     |
| PUT    | `/api/jds/{id}` | Admin | Update JD     |
| DELETE | `/api/jds/{id}` | Admin | Delete JD     |

### Analysis & Chat
| Method | Path              | Auth | Description         |
|--------|-------------------|------|---------------------|
| POST   | `/api/analyze`    | Any  | Upload CV + analyse |
| GET    | `/api/analyses`   | Any  | History (scoped)    |
| POST   | `/api/chat`       | Any  | SSE streaming chat  |
| GET    | `/api/stats`      | Any  | Dashboard stats     |
