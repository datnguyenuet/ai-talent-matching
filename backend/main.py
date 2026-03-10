"""
BirdMatchAI Backend — FastAPI + PostgreSQL + JWT Auth + Google OAuth + Claude AI
"""
import io, json, logging, os, re, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic
from google import genai
from google.genai import types as genai_types
import fitz
import docx as python_docx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import (
    create_access_token, exchange_google_code, get_current_user,
    get_current_user_optional, google_auth_url, hash_password,
    require_admin, verify_password,
    GOOGLE_CLIENT_ID, GOOGLE_REDIRECT_URI,
)
from database import CVAnalysis, JobDescription, User, get_db, init_db

log = logging.getLogger("talentos")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")

app = FastAPI(title="BirdMatchAI API", version="3.0.0", docs_url="/api/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

UPLOADS_DIR = Path("/app/uploads")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
FRONTEND_DIR = Path("/app/frontend")


# ── Startup ───────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    log.info("Initialising database…")
    await init_db()
    log.info("BirdMatchAI v3 ready ✓")


# ── AI provider selection ─────────────────────────────────
AI_PROVIDER = os.environ.get("AI_PROVIDER", "anthropic").lower()  # "anthropic" | "gemini"


# ── Anthropic ─────────────────────────────────────────────
def get_anthropic_client() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise HTTPException(503, "ANTHROPIC_API_KEY is not configured on the server")
    return anthropic.Anthropic(api_key=key)


# ── Gemini ────────────────────────────────────────────────
_raw_gemini_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()
GEMINI_MODEL = (
    _raw_gemini_model
    if _raw_gemini_model.startswith("models/")
    else f"models/{_raw_gemini_model}"
)


def get_gemini_client() -> genai.Client:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise HTTPException(503, "GEMINI_API_KEY is not configured on the server")
    return genai.Client(api_key=key)


# ════════════════════════════════════════════════════════════
#  SCHEMAS
# ════════════════════════════════════════════════════════════
class SignUpRequest(BaseModel):
    email: str
    password: str
    name: str = ""


class SignInRequest(BaseModel):
    email: str
    password: str


class JDPayload(BaseModel):
    title: str
    department: str
    location: str = ""
    type: str = "Full-time"
    experience: str = ""
    salary: str = ""
    status: str = "draft"
    summary: str = ""
    responsibilities: str = ""
    requirements: str = ""
    niceToHave: str = ""
    benefits: str = ""
    skills: list[str] = []


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    jd_id: str
    messages: list[ChatMessage]
    question: str


# ════════════════════════════════════════════════════════════
#  AUTH ENDPOINTS
# ════════════════════════════════════════════════════════════

@app.post("/api/auth/signup", status_code=201)
async def signup(body: SignUpRequest, db: AsyncSession = Depends(get_db)):
    existing = (await db.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    if existing:
        raise HTTPException(400, "Email already registered")
    if len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    user = User(
        email=body.email.lower(), name=body.name or body.email.split("@")[0],
        role="user", provider="local", hashed_password=hash_password(body.password),
    )
    db.add(user)
    await db.flush()
    token = create_access_token({"sub": str(user.id), "email": user.email, "role": user.role, "name": user.name})
    log.info("New signup: %s", user.email)
    return {"token": token, "user": user.to_dict()}


@app.post("/api/auth/signin")
async def signin(body: SignInRequest, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    if not user or not user.hashed_password or not verify_password(body.password, user.hashed_password):
        raise HTTPException(401, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(403, "Account is disabled")
    user.last_login = datetime.now(timezone.utc)
    token = create_access_token(
        {"sub": str(user.id), "email": user.email, "role": user.role, "name": user.name, "avatar": user.avatar or ""})
    log.info("Login: %s (%s)", user.email, user.role)
    return {"token": token, "user": user.to_dict()}


@app.get("/api/auth/me")
async def me(payload: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.id == uuid.UUID(payload["sub"])))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    return user.to_dict()


# ── Google OAuth ──────────────────────────────────────────
def _callback_uri(request: Request) -> str:
    """Use GOOGLE_REDIRECT_URI env var if set, otherwise derive from request."""
    if GOOGLE_REDIRECT_URI:
        return GOOGLE_REDIRECT_URI
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/auth/google/callback"


@app.get("/api/auth/google")
async def google_login(request: Request):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(503, "Google OAuth not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.")
    return JSONResponse({"url": google_auth_url(_callback_uri(request))})


@app.get("/api/auth/google/callback")
async def google_callback(code: str, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        guser = await exchange_google_code(code, _callback_uri(request))
    except Exception as e:
        raise HTTPException(400, f"Google auth failed: {e}")

    google_id = guser.get("sub")
    email = guser.get("email", "").lower()
    name = guser.get("name", "")
    avatar = guser.get("picture", "")

    # Find by google_id first, then by email
    user = (await db.execute(select(User).where(User.google_id == google_id))).scalar_one_or_none()
    if not user:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()

    if user:
        # Update Google info
        user.google_id = google_id
        user.avatar = avatar
        user.name = user.name or name
        user.provider = "google" if not user.hashed_password else user.provider
        user.last_login = datetime.now(timezone.utc)
    else:
        user = User(email=email, name=name, avatar=avatar, role="user",
                    provider="google", google_id=google_id)
        db.add(user)
        await db.flush()

    token = create_access_token(
        {"sub": str(user.id), "email": user.email, "role": user.role, "name": user.name, "avatar": user.avatar or ""})
    log.info("Google login: %s", user.email)
    # Redirect to frontend with token
    return RedirectResponse(url=f"/?token={token}")


# ── Admin: list users ──────────────────────────────────────
@app.get("/api/admin/users")
async def list_users(_: dict = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    users = (await db.execute(select(User).order_by(User.created_at.desc()))).scalars().all()
    return [u.to_dict() for u in users]


# ════════════════════════════════════════════════════════════
#  JD ENDPOINTS — read: any auth; write: admin only
# ════════════════════════════════════════════════════════════

@app.get("/api/jds")
async def list_jds(
        status: Optional[str] = None,
        _: dict = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
):
    q = select(JobDescription).order_by(JobDescription.created_at.desc())
    if status:
        q = q.where(JobDescription.status == status)
    return [(jd.to_dict()) for jd in (await db.execute(q)).scalars().all()]


@app.get("/api/jds/{jd_id}")
async def get_jd(jd_id: str, _: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    jd = await db.get(JobDescription, uuid.UUID(jd_id))
    if not jd: raise HTTPException(404, "JD not found")
    return jd.to_dict()


@app.post("/api/jds", status_code=201)
async def create_jd(payload: JDPayload, admin: dict = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    jd = JobDescription(
        title=payload.title, department=payload.department, location=payload.location,
        type=payload.type, experience=payload.experience, salary=payload.salary,
        status=payload.status, summary=payload.summary, responsibilities=payload.responsibilities,
        requirements=payload.requirements, nice_to_have=payload.niceToHave,
        benefits=payload.benefits, skills=payload.skills, created_at=now, updated_at=now,
    )
    db.add(jd)
    await db.flush()
    log.info("Admin %s created JD: %s", admin["email"], jd.title)
    return jd.to_dict()


@app.put("/api/jds/{jd_id}")
async def update_jd(jd_id: str, payload: JDPayload, admin: dict = Depends(require_admin),
                    db: AsyncSession = Depends(get_db)):
    jd = await db.get(JobDescription, uuid.UUID(jd_id))
    if not jd: raise HTTPException(404, "JD not found")
    for field, col in [("title", "title"), ("department", "department"), ("location", "location"), ("type", "type"),
                       ("experience", "experience"), ("salary", "salary"), ("status", "status"), ("summary", "summary"),
                       ("responsibilities", "responsibilities"), ("requirements", "requirements"),
                       ("benefits", "benefits"), ("skills", "skills")]:
        setattr(jd, col, getattr(payload, field))
    jd.nice_to_have = payload.niceToHave
    jd.updated_at = datetime.now(timezone.utc)
    log.info("Admin %s updated JD: %s", admin["email"], jd_id)
    return jd.to_dict()


@app.delete("/api/jds/{jd_id}", status_code=204)
async def delete_jd(jd_id: str, admin: dict = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    jd = await db.get(JobDescription, uuid.UUID(jd_id))
    if not jd: raise HTTPException(404, "JD not found")
    await db.delete(jd)
    log.info("Admin %s deleted JD: %s", admin["email"], jd_id)


# ════════════════════════════════════════════════════════════
#  CV ANALYSIS — auth required; users see only their own
# ════════════════════════════════════════════════════════════
def extract_text(file_bytes: bytes, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    try:
        if ext == ".pdf":
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            return "\n".join(p.get_text() for p in doc)
        elif ext == ".docx":
            d = python_docx.Document(io.BytesIO(file_bytes))
            return "\n".join(p.text for p in d.paragraphs if p.text.strip())
        else:
            return file_bytes.decode("utf-8", errors="ignore")
    except Exception as e:
        log.warning("Extract failed %s: %s", filename, e)
        return ""


@app.post("/api/analyze")
async def analyze_cv(
        jd_id: str = Form(...),
        cv_file: UploadFile = File(...),
        payload: dict = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
):
    jd = await db.get(JobDescription, uuid.UUID(jd_id))
    if not jd: raise HTTPException(404, "JD not found")

    file_bytes = await cv_file.read()
    (UPLOADS_DIR / f"{uuid.uuid4()}_{cv_file.filename}").write_bytes(file_bytes)
    cv_text = extract_text(
        file_bytes,
        cv_file.filename
    ) or f"[Binary/scanned file: {cv_file.filename}. No extractable text — performing contextual analysis.]"

    system_prompt = (
        "You are an expert senior recruitment analyst with 15+ years of experience "
        "in technical and non-technical hiring. Analyse the CV against the job description "
        "and return ONLY valid JSON — no markdown fences, no preamble, no trailing text."
    )

    user_prompt = f"""Job Description:
Title: {jd.title}
Department: {jd.department}
Experience Required: {jd.experience}
Required Skills: {', '.join(jd.skills or [])}
Requirements:
{jd.requirements}
Responsibilities:
{jd.responsibilities}
Nice to Have:
{jd.nice_to_have or 'N/A'}

Candidate CV:
---
{cv_text[:6000]}
---

Return exactly this JSON schema (integers for numeric fields):
{{
  "candidateName": "<extract from CV or 'Unknown'>",
  "currentTitle": "<current or most recent role>",
  "yearsExperience": "<e.g. 4 years>",
  "score": <0-100>,
  "scoreBreakdown": {{
    "skills": <0-100>,
    "experience": <0-100>,
    "education": <0-100>,
    "cultureFit": <0-100>
  }},
  "matches": ["<specific matching point>"],
  "gaps": ["<specific gap>"],
  "strengths": ["<notable strength>"],
  "summary": "<3-4 sentence professional assessment>",
  "recommendation": "strong_yes | yes | maybe | no",
  "suggestions": "<2-3 actionable recommendations for the hiring manager>"
}}"""

    raw = ""
    try:
        if AI_PROVIDER == "gemini":
            gemini = get_gemini_client()
            resp = gemini.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=1500,
                ),
            )
            raw = (resp.text or "").strip()
        else:
            client = get_anthropic_client()
            resp = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=1500,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = resp.content[0].text.strip()

        if not raw:
            raise HTTPException(500, "AI returned an empty response")

        raw = re.sub(r"^```json\s*|```$", "", raw, flags=re.MULTILINE).strip()
        result = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            result = json.loads(m.group())
        else:
            raise HTTPException(500, f"AI returned malformed JSON: {raw[:300]}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))

    # Persist to PostgreSQL
    analysis = CVAnalysis(
        jd_id=jd.id,
        user_id=uuid.UUID(payload["sub"]),
        jd_title=jd.title,
        filename=cv_file.filename,
        result=result,
        score=int(result.get("score", 0)),
        candidate_name=result.get("candidateName", ""),
        recommendation=result.get("recommendation", "maybe"),
    )
    db.add(analysis)
    await db.flush()
    log.info("CV analysed by %s for '%s' → %s%%", payload["email"], jd.title, result.get("score"))
    return {"analysis": result, "record_id": str(analysis.id)}


@app.get("/api/analyses")
async def list_analyses(
        jd_id: Optional[str] = None,
        limit: int = 50,
        payload: dict = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
):
    q = select(CVAnalysis).order_by(CVAnalysis.created_at.desc()).limit(limit)
    # Non-admins only see their own analyses
    if payload.get("role") != "admin":
        q = q.where(CVAnalysis.user_id == uuid.UUID(payload["sub"]))
    if jd_id:
        q = q.where(CVAnalysis.jd_id == uuid.UUID(jd_id))
    return [(a.to_dict()) for a in (await db.execute(q)).scalars().all()]


# ════════════════════════════════════════════════════════════
#  CHAT — auth required
# ════════════════════════════════════════════════════════════
@app.post("/api/chat")
async def chat(payload: ChatRequest, auth: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    jd = await db.get(JobDescription, uuid.UUID(payload.jd_id))
    if not jd:
        raise HTTPException(404, "JD not found")

    system_prompt = f"""You are TalentOS AI, an expert recruitment assistant built into a recruitment management platform.

You have full context about this specific role:

═══ JOB DESCRIPTION ═══
Title:      {jd.title}
Department: {jd.department}
Location:   {jd.location}
Type:       {jd.type}
Experience: {jd.experience}
Salary:     {jd.salary or 'Not specified'}
Status:     {jd.status}

Required Skills: {', '.join(jd.skills or [])}

Summary:
{jd.summary}

Responsibilities:
{jd.responsibilities}

Requirements:
{jd.requirements}

Nice to Have:
{jd.nice_to_have or 'None listed'}

Benefits:
{jd.benefits or 'Not specified'}
═══════════════════════

Answer questions about this role concisely and professionally.
Use **bold** for key terms. Use bullet points for lists. Be specific and actionable."""

    history = [
        {"role": m.role, "content": m.content}
        for m in payload.messages[-14:]
    ]

    if AI_PROVIDER == "gemini":
        gemini = get_gemini_client()
        # Build contents list: history + new question
        gemini_contents = [
            genai_types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[genai_types.Part(text=m["content"])],
            )
            for m in history
        ]
        gemini_contents.append(
            genai_types.Content(role="user", parts=[genai_types.Part(text=payload.question)])
        )

        async def event_stream():
            try:
                for chunk in gemini.models.generate_content_stream(
                        model=GEMINI_MODEL,
                        contents=gemini_contents,
                        config=genai_types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            max_output_tokens=1024,
                        ),
                ):
                    if chunk.text:
                        yield f"data: {json.dumps({'delta': chunk.text})}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"
    else:
        client = get_anthropic_client()

        async def event_stream():
            try:
                with client.messages.stream(
                        model="claude-opus-4-5",
                        max_tokens=1024,
                        system=system_prompt,
                        messages=[*history, {"role": "user", "content": payload.question}],
                ) as stream:
                    for chunk in stream.text_stream:
                        yield f"data: {json.dumps({'delta': chunk})}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ════════════════════════════════════════════════════════════
#  STATS
# ════════════════════════════════════════════════════════════
@app.get("/api/stats")
async def stats(auth: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    is_admin = auth.get("role") == "admin"
    total_jds = (await db.execute(select(func.count()).select_from(JobDescription))).scalar()
    active_jds = (await db.execute(
        select(func.count()).select_from(JobDescription).where(JobDescription.status == "active"))).scalar()

    # Analysis stats scoped to user if not admin
    base_q = select(CVAnalysis)
    if not is_admin:
        base_q = base_q.where(CVAnalysis.user_id == uuid.UUID(auth["sub"]))

    total_analyses = (await db.execute(select(func.count()).select_from(base_q.subquery()))).scalar()
    avg_score_r = (await db.execute(select(func.avg(CVAnalysis.score)).select_from(base_q.subquery()))).scalar()

    recent_q = base_q.order_by(CVAnalysis.created_at.desc()).limit(5)
    recent = (await db.execute(recent_q)).scalars().all()

    return {
        "totalJDs": total_jds,
        "activeJDs": active_jds,
        "totalAnalyses": total_analyses,
        "avgMatchScore": round(float(avg_score_r), 1) if avg_score_r else 0,
        "recentAnalyses": [a.to_dict() for a in recent],
        "isAdmin": is_admin,
    }


@app.get("/health")
def health():
    return {"status": "ok", "version": "3.0.0"}


# ── Frontend fallback ──────────────────────────────────────
@app.get("/")
def root():
    idx = FRONTEND_DIR / "index.html"
    return FileResponse(idx) if idx.exists() else {"message": "BirdMatchAI API", "docs": "/api/docs"}

# if FRONTEND_DIR.exists():
#     try:
#         app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")
#     except Exception:
#         pass
#
#
#     @app.get("/{full_path:path}")
#     def spa(full_path: str):
#         idx = FRONTEND_DIR / "index.html"
#         return FileResponse(idx) if idx.exists() else HTTPException(404)
