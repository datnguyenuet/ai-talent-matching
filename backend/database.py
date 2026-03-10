"""
TalentOS — PostgreSQL database layer (async SQLAlchemy 2.x)
"""
import os
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator

from sqlalchemy import (
    Column, String, Text, DateTime, Integer, Float,
    JSON, ForeignKey, text
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine
)
from sqlalchemy.orm import DeclarativeBase, relationship

# ── Connection URL ─────────────────────────────────────────
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://talentos:talentos@db:5432/talentos"
)

# ── Engine ────────────────────────────────────────────────
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# ── Base ──────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass

# ══════════════════════════════════════════════════════════
#  MODELS
# ══════════════════════════════════════════════════════════

class JobDescription(Base):
    __tablename__ = "job_descriptions"

    id          = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title       = Column(String(255), nullable=False, index=True)
    department  = Column(String(100), nullable=False)
    location    = Column(String(200), default="")
    type        = Column(String(50),  default="Full-time")
    experience  = Column(String(100), default="")
    salary      = Column(String(100), default="")
    status      = Column(String(20),  default="draft", index=True)

    summary          = Column(Text, default="")
    responsibilities = Column(Text, default="")
    requirements     = Column(Text, default="")
    nice_to_have     = Column(Text, default="")
    benefits         = Column(Text, default="")
    skills           = Column(JSON, default=list)   # stored as JSON array

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    analyses = relationship("CVAnalysis", back_populates="jd",
                            cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        return {
            "id":              str(self.id),
            "title":           self.title,
            "department":      self.department,
            "location":        self.location or "",
            "type":            self.type,
            "experience":      self.experience or "",
            "salary":          self.salary or "",
            "status":          self.status,
            "summary":         self.summary or "",
            "responsibilities": self.responsibilities or "",
            "requirements":    self.requirements or "",
            "niceToHave":      self.nice_to_have or "",
            "benefits":        self.benefits or "",
            "skills":          self.skills or [],
            "createdAt":       self.created_at.isoformat() if self.created_at else None,
            "updatedAt":       self.updated_at.isoformat() if self.updated_at else None,
        }


class CVAnalysis(Base):
    __tablename__ = "cv_analyses"

    id       = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    jd_id    = Column(PGUUID(as_uuid=True), ForeignKey("job_descriptions.id", ondelete="CASCADE"),
                      nullable=False, index=True)
    jd_title = Column(String(255), default="")
    filename = Column(String(500), default="")

    # Claude's full structured result
    result   = Column(JSON, nullable=False)

    # Denormalised for fast stats queries
    score             = Column(Integer, default=0)
    candidate_name    = Column(String(200), default="")
    recommendation    = Column(String(20),  default="maybe")

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    jd = relationship("JobDescription", back_populates="analyses")

    def to_dict(self) -> dict:
        return {
            "id":            str(self.id),
            "jd_id":         str(self.jd_id),
            "jd_title":      self.jd_title,
            "filename":      self.filename,
            "result":        self.result,
            "score":         self.score,
            "candidateName": self.candidate_name,
            "recommendation": self.recommendation,
            "createdAt":     self.created_at.isoformat() if self.created_at else None,
        }


# ── Session dependency ────────────────────────────────────
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Init DB (create tables + seed) ───────────────────────
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed sample JDs if table is empty
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM job_descriptions"))
        count = result.scalar()
        if count == 0:
            now = datetime.now(timezone.utc)
            samples = [
                JobDescription(
                    title="Senior Frontend Engineer",
                    department="Engineering",
                    location="Remote / Ho Chi Minh City",
                    type="Full-time",
                    experience="Senior (5-8 yrs)",
                    salary="$3,000 – $4,500/month",
                    status="active",
                    summary="We are looking for a Senior Frontend Engineer to help build our next-generation product platform. You will work closely with design and backend teams to ship high-quality, performant user interfaces.",
                    responsibilities="- Lead frontend architecture decisions\n- Build reusable component libraries\n- Mentor junior engineers\n- Collaborate with product and design teams\n- Performance optimisation and code reviews",
                    requirements="- 5+ years of frontend development experience\n- Expert-level React or Vue.js\n- Strong TypeScript skills\n- Experience with state management (Redux, Pinia, Zustand)\n- CI/CD and testing practices",
                    nice_to_have="- Experience with micro-frontends\n- GraphQL knowledge\n- Design systems experience",
                    benefits="Competitive salary, flexible remote work, health insurance, learning budget, 15 days annual leave.",
                    skills=["React", "TypeScript", "Vue.js", "GraphQL", "Node.js", "CSS", "Git", "Testing"],
                    created_at=now, updated_at=now,
                ),
                JobDescription(
                    title="Product Manager",
                    department="Product",
                    location="Hanoi, Vietnam",
                    type="Full-time",
                    experience="Mid-level (3-5 yrs)",
                    salary="$2,500 – $3,500/month",
                    status="active",
                    summary="Drive product strategy and execution for our B2B SaaS platform.",
                    responsibilities="- Define product roadmap and prioritisation\n- Write clear product requirements\n- Conduct user research and interviews\n- Analyse metrics and define KPIs\n- Coordinate cross-functional teams",
                    requirements="- 3+ years product management experience\n- Strong analytical skills\n- B2B SaaS experience\n- Excellent communication\n- SQL knowledge preferred",
                    nice_to_have="- Technical background\n- Agile/Scrum experience\n- Startup experience",
                    benefits="Stock options, health coverage, flexible hours, monthly learning allowance.",
                    skills=["Product Strategy", "Agile", "SQL", "Figma", "Analytics", "User Research"],
                    created_at=now, updated_at=now,
                ),
            ]
            session.add_all(samples)
            await session.commit()
