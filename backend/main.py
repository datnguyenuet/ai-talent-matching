"""
TalentOS Backend — FastAPI + PostgreSQL (asyncpg) + Claude AI / Gemini AI
"""
import io
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic
from google import genai
from google.genai import types as genai_types
import fitz  # PyMuPDF
import docx as python_docx
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import CVAnalysis, JobDescription, get_db, init_db

# ── Logging ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("talentos")

# ── App ───────────────────────────────────────────────────
app = FastAPI(title="TalentOS API", version="2.0.0", docs_url="/api/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOADS_DIR = Path("/app/uploads")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

FRONTEND_DIR = Path("/app/frontend")


# ── Startup ───────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    log.info("Initialising database…")
    await init_db()
    log.info("TalentOS ready ✓")


# ── AI provider selection ─────────────────────────────────
AI_PROVIDER = os.environ.get("AI_PROVIDER", "anthropic").lower()  # "anthropic" | "gemini"


# ── Anthropic ─────────────────────────────────────────────
def get_anthropic_client() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise HTTPException(503, "ANTHROPIC_API_KEY is not configured on the server")
    return anthropic.Anthropic(api_key=key)


# ── Gemini ────────────────────────────────────────────────
_raw_gemini_model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash").strip()
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


# ── Pydantic schemas ──────────────────────────────────────
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


# ── CV text extraction ────────────────────────────────────
def extract_text(file_bytes: bytes, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    try:
        if ext == ".pdf":
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            return "\n".join(page.get_text() for page in doc)
        elif ext == ".docx":
            d = python_docx.Document(io.BytesIO(file_bytes))
            return "\n".join(p.text for p in d.paragraphs if p.text.strip())
        else:
            return file_bytes.decode("utf-8", errors="ignore")
    except Exception as exc:
        log.warning("Text extraction failed for %s: %s", filename, exc)
        return ""


# ════════════════════════════════════════════════════════════
#  JD ENDPOINTS
# ════════════════════════════════════════════════════════════

@app.get("/api/jds")
async def list_jds(
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(JobDescription).order_by(JobDescription.created_at.desc())
    if status:
        q = q.where(JobDescription.status == status)
    result = await db.execute(q)
    return [jd.to_dict() for jd in result.scalars().all()]


@app.get("/api/jds/{jd_id}")
async def get_jd(jd_id: str, db: AsyncSession = Depends(get_db)):
    jd = await db.get(JobDescription, uuid.UUID(jd_id))
    if not jd:
        raise HTTPException(404, "JD not found")
    return jd.to_dict()


@app.post("/api/jds", status_code=201)
async def create_jd(payload: JDPayload, db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    jd = JobDescription(
        title=payload.title,
        department=payload.department,
        location=payload.location,
        type=payload.type,
        experience=payload.experience,
        salary=payload.salary,
        status=payload.status,
        summary=payload.summary,
        responsibilities=payload.responsibilities,
        requirements=payload.requirements,
        nice_to_have=payload.niceToHave,
        benefits=payload.benefits,
        skills=payload.skills,
        created_at=now,
        updated_at=now,
    )
    db.add(jd)
    await db.flush()
    log.info("Created JD: %s [%s]", jd.title, jd.id)
    return jd.to_dict()


@app.put("/api/jds/{jd_id}")
async def update_jd(
    jd_id: str,
    payload: JDPayload,
    db: AsyncSession = Depends(get_db),
):
    jd = await db.get(JobDescription, uuid.UUID(jd_id))
    if not jd:
        raise HTTPException(404, "JD not found")

    jd.title           = payload.title
    jd.department      = payload.department
    jd.location        = payload.location
    jd.type            = payload.type
    jd.experience      = payload.experience
    jd.salary          = payload.salary
    jd.status          = payload.status
    jd.summary         = payload.summary
    jd.responsibilities = payload.responsibilities
    jd.requirements    = payload.requirements
    jd.nice_to_have    = payload.niceToHave
    jd.benefits        = payload.benefits
    jd.skills          = payload.skills
    jd.updated_at      = datetime.now(timezone.utc)

    log.info("Updated JD: %s", jd_id)
    return jd.to_dict()


@app.delete("/api/jds/{jd_id}", status_code=204)
async def delete_jd(jd_id: str, db: AsyncSession = Depends(get_db)):
    jd = await db.get(JobDescription, uuid.UUID(jd_id))
    if not jd:
        raise HTTPException(404, "JD not found")
    await db.delete(jd)
    log.info("Deleted JD: %s", jd_id)


# ════════════════════════════════════════════════════════════
#  CV ANALYSIS
# ════════════════════════════════════════════════════════════

@app.post("/api/analyze")
async def analyze_cv(
    jd_id: str = Form(...),
    cv_file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    jd = await db.get(JobDescription, uuid.UUID(jd_id))
    if not jd:
        raise HTTPException(404, "JD not found")

    file_bytes = await cv_file.read()

    # Persist upload
    save_path = UPLOADS_DIR / f"{uuid.uuid4()}_{cv_file.filename}"
    save_path.write_bytes(file_bytes)

    cv_text = extract_text(file_bytes, cv_file.filename)
    if not cv_text.strip():
        cv_text = (
            f"[Binary/scanned file: {cv_file.filename}. "
            "No extractable text — performing contextual analysis.]"
        )

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
            raw = resp.text.strip()
        else:
            client = get_anthropic_client()
            resp = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=1500,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = resp.content[0].text.strip()

        raw = re.sub(r"^```json\s*|```$", "", raw, flags=re.MULTILINE).strip()
        result = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            result = json.loads(m.group())
        else:
            raise HTTPException(500, f"AI returned malformed JSON: {raw[:300]}")
    except Exception as exc:
        raise HTTPException(500, str(exc))

    # Persist to PostgreSQL
    analysis = CVAnalysis(
        jd_id=jd.id,
        jd_title=jd.title,
        filename=cv_file.filename,
        result=result,
        score=int(result.get("score", 0)),
        candidate_name=result.get("candidateName", ""),
        recommendation=result.get("recommendation", "maybe"),
    )
    db.add(analysis)
    await db.flush()

    log.info(
        "Analysed '%s' for '%s' → %s%%", cv_file.filename, jd.title, result.get("score")
    )
    return {"analysis": result, "record_id": str(analysis.id)}


# ════════════════════════════════════════════════════════════
#  CHAT — STREAMING SSE
# ════════════════════════════════════════════════════════════

@app.post("/api/chat")
async def chat_with_jd(
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
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
async def get_stats(db: AsyncSession = Depends(get_db)):
    total_jds   = (await db.execute(select(func.count()).select_from(JobDescription))).scalar()
    active_jds  = (await db.execute(
        select(func.count()).select_from(JobDescription).where(JobDescription.status == "active")
    )).scalar()
    draft_jds   = (await db.execute(
        select(func.count()).select_from(JobDescription).where(JobDescription.status == "draft")
    )).scalar()
    total_analyses = (await db.execute(select(func.count()).select_from(CVAnalysis))).scalar()
    avg_score   = (await db.execute(select(func.avg(CVAnalysis.score)))).scalar()

    recent_q = (
        select(CVAnalysis)
        .order_by(CVAnalysis.created_at.desc())
        .limit(5)
    )
    recent = (await db.execute(recent_q)).scalars().all()

    return {
        "totalJDs":       total_jds,
        "activeJDs":      active_jds,
        "draftJDs":       draft_jds,
        "totalAnalyses":  total_analyses,
        "avgMatchScore":  round(float(avg_score), 1) if avg_score else 0,
        "recentAnalyses": [a.to_dict() for a in recent],
    }


# ════════════════════════════════════════════════════════════
#  ANALYSES HISTORY
# ════════════════════════════════════════════════════════════

@app.get("/api/analyses")
async def list_analyses(
    jd_id: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    q = select(CVAnalysis).order_by(CVAnalysis.created_at.desc()).limit(limit)
    if jd_id:
        q = q.where(CVAnalysis.jd_id == uuid.UUID(jd_id))
    result = await db.execute(q)
    return [a.to_dict() for a in result.scalars().all()]


# ════════════════════════════════════════════════════════════
#  FRONTEND (served by FastAPI if nginx not in front)
# ════════════════════════════════════════════════════════════

@app.get("/health")
def health():
    return {"status": "ok", "version": "2.0.0"}


@app.get("/")
def serve_index():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"message": "TalentOS API is running", "docs": "/api/docs"}


# if FRONTEND_DIR.exists():
#     _assets_dir = FRONTEND_DIR / "assets"
#     if _assets_dir.exists():
#         app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")
#     # Catch-all for SPA routing — must be LAST
#     @app.get("/{full_path:path}")
#     def spa_fallback(full_path: str):
#         index = FRONTEND_DIR / "index.html"
#         if index.exists():
#             return FileResponse(index)
#         raise HTTPException(404)
