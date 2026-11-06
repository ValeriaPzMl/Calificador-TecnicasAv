"""RLS en `alembic_version`.

Supabase expone por la API (PostgREST) todas las tablas de `public`, y
`alembic_version` (la que usa Alembic para saber qué migraciones corrió) no
tenía Row Level Security. Se activa sin políticas: con la llave publishable
nadie puede leerla ni cambiarla; Alembic entra como `postgres`, que no
está sujeto a RLS, así que sigue funcionando.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-06
"""

import logging
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        logger.info("no es Postgres: sin RLS")
        return
    op.execute("ALTER TABLE public.alembic_version ENABLE ROW LEVEL SECURITY")
    logger.info("RLS activado en alembic_version")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE public.alembic_version DISABLE ROW LEVEL SECURITY")
