"""Tablas iniciales: exámenes, secciones, preguntas y resultados.

Las mismas tablas de `storage/tables.py` y, solo en Postgres (Supabase):
- `owner_id` con FK a `auth.users`: si se borra un usuario, se borran sus
  exámenes y resultados.
- Row Level Security: con la llave publishable de Supabase, cada usuario
  solo puede leer y cambiar sus propias filas. El backend entra como
  `postgres` (no le aplica RLS) y filtra por `owner_id` en la API; RLS es
  la segunda capa, por si alguien consulta las tablas directo.

Revision ID: 0001
Revises:
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
TZ = sa.DateTime(timezone=True)

# Cada tabla: condición para que la fila sea del usuario con sesión
# (`auth.uid()`). `(select auth.uid())` se evalúa una vez por consulta.
OWNER_RULES = {
    "exams": "owner_id = (select auth.uid())",
    "results": "owner_id = (select auth.uid())",
    "sections": "exists (select 1 from public.exams e "
                "where e.id = exam_id and e.owner_id = (select auth.uid()))",
    "questions": "exists (select 1 from public.sections s join public.exams e on e.id = s.exam_id "
                 "where s.id = section_id and e.owner_id = (select auth.uid()))",
}


def upgrade() -> None:
    op.create_table(
        "exams",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("num_options", sa.Integer(), nullable=False),
        sa.Column("student_id_digits", sa.Integer(), nullable=False),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("updated_at", TZ, nullable=False),
    )
    op.create_index("ix_exams_owner_id", "exams", ["owner_id"])

    op.create_table(
        "sections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("exam_id", sa.String(64), sa.ForeignKey("exams.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("exam_id", "key"),
    )
    op.create_index("ix_sections_exam_id", "sections", ["exam_id"])

    op.create_table(
        "questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("section_id", sa.Uuid(), sa.ForeignKey("sections.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("correct_answer", sa.String(1), nullable=False),
        sa.Column("points", sa.Float(), nullable=False),
    )
    op.create_index("ix_questions_section_id", "questions", ["section_id"])

    op.create_table(
        "results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("exam_id", sa.String(64), sa.ForeignKey("exams.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.String(32), nullable=True),
        sa.Column("student_id_raw", sa.String(32), nullable=False),
        sa.Column("final_score", sa.Float(), nullable=False),
        sa.Column("max_score", sa.Float(), nullable=False),
        sa.Column("section_scores", JsonB, nullable=False),
        sa.Column("answers", JsonB, nullable=False),
        sa.Column("ambiguous", JsonB, nullable=False),
        sa.Column("blank", JsonB, nullable=False),
        sa.Column("fills", JsonB, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("image_path", sa.Text(), nullable=True),
        sa.Column("scanned_at", TZ, nullable=False),
    )
    op.create_index("ix_results_exam_id", "results", ["exam_id"])
    op.create_index("ix_results_owner_id", "results", ["owner_id"])
    op.create_index("one_selected_result_per_student", "results", ["exam_id", "student_id"],
                    unique=True, postgresql_where=sa.text("selected"),
                    sqlite_where=sa.text("selected"))

    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite: no hay auth.users ni RLS

    for table in ("exams", "results"):
        op.create_foreign_key(f"{table}_owner_id_fkey", table, "users", ["owner_id"], ["id"],
                              referent_schema="auth", ondelete="CASCADE")
    for table, rule in OWNER_RULES.items():
        op.execute(f"alter table public.{table} enable row level security")
        op.execute(f"create policy {table}_del_dueno on public.{table} for all "
                   f"to authenticated using ({rule}) with check ({rule})")


def downgrade() -> None:
    for table in ("results", "questions", "sections", "exams"):
        op.drop_table(table)  # borra también índices, FKs y políticas
