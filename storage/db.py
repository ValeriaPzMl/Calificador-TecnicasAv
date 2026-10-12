"""
Conexión a la base de datos.

La misma capa sirve para desarrollo y para producción; solo cambia
`DATABASE_URL`:

- Desarrollo (por defecto): SQLite en `data/calificador.db`. Un archivo,
  nada que instalar.
- Pruebas: SQLite en memoria (`sqlite://`), se borra al terminar.
- Producción (fase 4): la cadena de conexión de Postgres de Supabase,
  ej. `postgresql+psycopg://postgres:...@db.<proyecto>.supabase.co:5432/postgres`.

Los tipos de las tablas (`tables.py`) son los de Postgres (uuid, jsonb,
timestamptz) con equivalentes en SQLite, así que el esquema es el mismo.
"""

import logging
import os
import uuid
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .tables import Base

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "calificador.db"

# Mientras no hay login (fase 4), todo se guarda a nombre de este usuario.
# En Supabase, owner_id será el id (uuid) del usuario en auth.users.
LOCAL_OWNER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def database_url() -> str:
    url = os.environ.get("DATABASE_URL") or f"sqlite:///{DEFAULT_DB_PATH}"
    logger.debug("DATABASE_URL = %s", _hide_password(url))
    return url


def make_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    logger.info("conectando a %s", _hide_password(url))
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        # SQLite no respeta ON DELETE CASCADE a menos que se active
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return engine


def init_db(engine: Engine) -> None:
    """Crea las tablas que falten. En Postgres/Supabase esto lo harán
    migraciones (fase 4); para SQLite de desarrollo basta con esto."""
    logger.info("creando tablas que falten: %s", sorted(Base.metadata.tables))
    Base.metadata.create_all(engine)


def open_session(url: str | None = None) -> Session:
    """Atajo para el CLI: conecta, crea tablas si faltan y abre sesión."""
    engine = make_engine(url)
    init_db(engine)
    return sessionmaker(engine)()


def _hide_password(url: str) -> str:
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"
