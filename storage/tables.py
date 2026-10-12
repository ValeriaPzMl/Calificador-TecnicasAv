"""
Tablas de la base de datos (SQLAlchemy).

Diseñadas para Postgres/Supabase y que también funcionen en SQLite:
- ids de filas: `uuid` (en SQLite se guarda como texto).
- `exams.id` es texto corto porque va dentro del QR de la hoja (entre más
  corto, más grandes los cuadritos del QR y más fácil de leer).
- listas y diccionarios: `jsonb` en Postgres, JSON en SQLite.
- fechas: `timestamptz`, siempre en UTC.
- `owner_id`: uuid del usuario. En Supabase será `auth.users.id`; la FK a
  esa tabla y las políticas RLS se agregan en la migración de la fase 4
  (en SQLite no existe `auth.users`).

Reglas que viven aquí:
- Borrar un examen borra sus secciones, preguntas y resultados
  (ON DELETE CASCADE).
- Una matrícula puede tener varios resultados en un examen (se escaneó
  dos veces). Solo uno puede estar `selected`; mientras el profesor no
  elija, ninguno lo está. El índice único parcial lo garantiza.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JsonB = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


def local_time(dt: datetime) -> datetime:
    """Fecha guardada (UTC) en la hora local, para mostrarla. SQLite la
    regresa sin zona horaria; Postgres con zona."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone()


class Base(DeclarativeBase):
    pass


class ExamRow(Base):
    __tablename__ = "exams"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    title: Mapped[str] = mapped_column(Text)
    num_options: Mapped[int] = mapped_column(Integer)
    student_id_digits: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    sections: Mapped[list["SectionRow"]] = relationship(
        back_populates="exam", cascade="all, delete-orphan", passive_deletes=True,
        order_by="SectionRow.position",
    )
    results: Mapped[list["ResultRow"]] = relationship(
        back_populates="exam", cascade="all, delete-orphan", passive_deletes=True,
    )


class SectionRow(Base):
    __tablename__ = "sections"
    __table_args__ = (UniqueConstraint("exam_id", "key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(64))  # el `Section.id` del modelo, ej. "algebra"
    name: Mapped[str] = mapped_column(Text)
    weight: Mapped[float | None] = mapped_column(Float)
    position: Mapped[int] = mapped_column(Integer)

    exam: Mapped[ExamRow] = relationship(back_populates="sections")
    questions: Mapped[list["QuestionRow"]] = relationship(
        back_populates="section", cascade="all, delete-orphan", passive_deletes=True,
        order_by="QuestionRow.number",
    )


class QuestionRow(Base):
    __tablename__ = "questions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    section_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sections.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    correct_answer: Mapped[str] = mapped_column(String(1))
    points: Mapped[float] = mapped_column(Float)

    section: Mapped[SectionRow] = relationship(back_populates="questions")


class ResultRow(Base):
    __tablename__ = "results"
    __table_args__ = (
        Index(
            "one_selected_result_per_student", "exam_id", "student_id",
            unique=True,
            postgresql_where=text("selected"),
            sqlite_where=text("selected"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)  # para RLS sin joins
    student_id: Mapped[str | None] = mapped_column(String(32))  # None = no se leyó completa
    student_id_raw: Mapped[str] = mapped_column(String(32))
    final_score: Mapped[float] = mapped_column(Float)
    max_score: Mapped[float] = mapped_column(Float)
    section_scores: Mapped[list] = mapped_column(JsonB)
    answers: Mapped[dict] = mapped_column(JsonB)  # {"1": "A", "2": null, ...}
    ambiguous: Mapped[list] = mapped_column(JsonB)
    blank: Mapped[list] = mapped_column(JsonB)
    fills: Mapped[dict] = mapped_column(JsonB)  # oscuridad de cada burbuja, para revisar
    confidence: Mapped[float] = mapped_column(Float)
    needs_review: Mapped[bool] = mapped_column(Boolean)
    selected: Mapped[bool] = mapped_column(Boolean, default=True)
    image_path: Mapped[str | None] = mapped_column(Text)  # en Supabase: ruta en Storage
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    exam: Mapped[ExamRow] = relationship(back_populates="results")
