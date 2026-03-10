"""BirdMatchAI — PostgreSQL models: User, JobDescription, CVAnalysis"""
import os, uuid
from datetime import datetime, timezone
from typing import AsyncGenerator
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://talentos:talentos@db:5432/talentos")
engine = create_async_engine(DATABASE_URL, echo=False, pool_size=10, max_overflow=20, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# ── User ──────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"
    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(200), default="")
    avatar = Column(String(500), default="")
    role = Column(String(20), default="user")  # "admin" | "user"
    provider = Column(String(20), default="local")  # "local" | "google"
    google_id = Column(String(100), unique=True, nullable=True)
    hashed_password = Column(String(200), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_login = Column(DateTime(timezone=True), nullable=True)
    analyses = relationship("CVAnalysis", back_populates="user", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": str(self.id), "email": self.email, "name": self.name,
            "avatar": self.avatar or "", "role": self.role, "provider": self.provider,
            "isActive": self.is_active,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "lastLogin": self.last_login.isoformat() if self.last_login else None,
        }


# ── JobDescription ────────────────────────────────────────
class JobDescription(Base):
    __tablename__ = "job_descriptions"
    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(255), nullable=False, index=True)
    department = Column(String(100), nullable=False)
    location = Column(String(200), default="")
    type = Column(String(50), default="Full-time")
    experience = Column(String(100), default="")
    salary = Column(String(100), default="")
    status = Column(String(20), default="draft", index=True)
    summary = Column(Text, default="")
    responsibilities = Column(Text, default="")
    requirements = Column(Text, default="")
    nice_to_have = Column(Text, default="")
    benefits = Column(Text, default="")
    skills = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))
    analyses = relationship("CVAnalysis", back_populates="jd", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": str(self.id), "title": self.title, "department": self.department,
            "location": self.location or "", "type": self.type,
            "experience": self.experience or "", "salary": self.salary or "",
            "status": self.status, "summary": self.summary or "",
            "responsibilities": self.responsibilities or "",
            "requirements": self.requirements or "",
            "niceToHave": self.nice_to_have or "",
            "benefits": self.benefits or "", "skills": self.skills or [],
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }


# ── CVAnalysis ────────────────────────────────────────────
class CVAnalysis(Base):
    __tablename__ = "cv_analyses"
    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    jd_id = Column(PGUUID(as_uuid=True), ForeignKey("job_descriptions.id", ondelete="CASCADE"),
                   nullable=False, index=True)
    user_id = Column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=True, index=True)
    jd_title = Column(String(255), default="")
    filename = Column(String(500), default="")
    result = Column(JSON, nullable=False)
    score = Column(Integer, default=0)
    candidate_name = Column(String(200), default="")
    recommendation = Column(String(20), default="maybe")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    jd = relationship("JobDescription", back_populates="analyses")
    user = relationship("User", back_populates="analyses")

    def to_dict(self):
        return {
            "id": str(self.id), "jd_id": str(self.jd_id),
            "userId": str(self.user_id) if self.user_id else None,
            "jd_title": self.jd_title, "filename": self.filename,
            "result": self.result, "score": self.score,
            "candidateName": self.candidate_name,
            "recommendation": self.recommendation,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
        }


# ── DB session ────────────────────────────────────────────
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Init + seed ───────────────────────────────────────────
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        from auth import hash_password
        # Default admin
        if not (await session.execute(select(User).where(User.email == "admin@talentos.ai"))).scalar_one_or_none():
            session.add(User(
                email="admin@talentos.ai", name="Administrator",
                role="admin", provider="local",
                hashed_password=hash_password("123456"), is_active=True,
            ))
            await session.flush()
        # Sample JDs
        if (await session.execute(text("SELECT COUNT(*) FROM job_descriptions"))).scalar() == 0:
            now = datetime.now(timezone.utc)
            session.add_all([
                JobDescription(
                    title="Senior Frontend Engineer", department="Engineering",
                    location="Remote / Ho Chi Minh City", type="Full-time",
                    experience="Senior (5-8 yrs)", salary="$3,000–$4,500/month", status="active",
                    summary="Build next-generation product platform with a world-class engineering team.",
                    responsibilities="- Lead frontend architecture\n- Build reusable component libraries\n- Mentor junior engineers\n- Performance optimisation",
                    requirements="- 5+ years frontend experience\n- Expert React or Vue.js\n- Strong TypeScript\n- CI/CD practices",
                    nice_to_have="- Micro-frontend experience\n- GraphQL knowledge",
                    benefits="Competitive salary, remote work, health insurance, learning budget.",
                    skills=["React", "TypeScript", "Vue.js", "GraphQL", "Node.js", "CSS", "Git"],
                    created_at=now, updated_at=now,
                ),
                JobDescription(
                    title="Product Manager", department="Product",
                    location="Hanoi, Vietnam", type="Full-time",
                    experience="Mid-level (3-5 yrs)", salary="$2,500–$3,500/month", status="active",
                    summary="Drive product strategy and execution for our B2B SaaS platform.",
                    responsibilities="- Define product roadmap\n- Write clear requirements\n- Conduct user research\n- Analyse metrics",
                    requirements="- 3+ years PM experience\n- Strong analytical skills\n- B2B SaaS background\n- SQL knowledge",
                    nice_to_have="- Technical background\n- Startup experience",
                    benefits="Stock options, health coverage, flexible hours.",
                    skills=["Product Strategy", "Agile", "SQL", "Figma", "Analytics", "User Research"],
                    created_at=now, updated_at=now,
                ),
            ])
        await session.commit()
