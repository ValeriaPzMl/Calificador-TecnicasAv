"""
Conexión a la base de datos.

La misma capa sirve para desarrollo y para producción; solo cambia
`DATABASE_URL`:

- Desarrollo (por defecto): SQLite en `data/calificador.db`. Un archivo,
  nada que instalar.
- Pruebas: SQLite en memoria (`sqlite://`), se borra al terminar.
- Producción: la cadena de conexión de Postgres de Supabase (Session
  pooler), ej.
  `postgresql://postgres.<ref>:<password>@aws-0-<región>.pooler.supabase.com:5432/postgres`.
  Se pone en el archivo `.env` (no se sube a git) y se lee solo.

Los tipos de las tablas (`tables.py`) son los de Postgres (uuid, jsonb,
timestamptz) con equivalentes en SQLite, así que el esquema es el mismo.
En SQLite las tablas se crean solas (`init_db`); en Postgres las crea la
migración (`alembic upgrade head`), que además agrega lo que solo existe
en Supabase (FK a `auth.users` y Row Level Security).

Dueño de los datos (`owner_id`): la API usa el usuario que inició sesión.
El CLI usa `default_owner_id()`: `CALIFICADOR_OWNER_ID` del `.env` (el uuid
de tu usuario en Supabase) o, si no está, el usuario local fijo.
"""

import logging
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

from .tables import Base

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_DIR / "data" / "calificador.db"
ENV_PATH = PROJECT_DIR / ".env"

# Sin Supabase (SQLite local), todo se guarda a nombre de este usuario.
# En Supabase, owner_id es el id (uuid) del usuario en auth.users.
LOCAL_OWNER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

# Las variables que ya estén definidas en el sistema ganan sobre el .env
load_dotenv(ENV_PATH, override=False)


def database_url() -> str:
    url = os.environ.get("DATABASE_URL") or f"sqlite:///{DEFAULT_DB_PATH}"
    # Supabase da la URL como postgresql://; SQLAlchemy necesita saber el driver
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url[len(prefix):]
    logger.debug("DATABASE_URL = %s", _hide_password(url))
    return url


def default_owner_id() -> uuid.UUID:
    """Dueño de lo que se crea desde el CLI: `CALIFICADOR_OWNER_ID` o el
    usuario local."""
    value = os.environ.get("CALIFICADOR_OWNER_ID")
    if not value:
        return LOCAL_OWNER_ID
    try:
        return uuid.UUID(value)
    except ValueError:
        raise SystemExit(f"ERROR: CALIFICADOR_OWNER_ID no es un uuid válido: {value!r}") from None


def make_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    logger.info("conectando a %s", _hide_password(url))
    if url.startswith("postgresql"):
        # El pooler de Supabase cierra conexiones inactivas: se revisan antes de usarlas
        return create_engine(url, pool_pre_ping=True, pool_recycle=300)
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        # SQLite no respeta ON DELETE CASCADE a menos que se active
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return engine


def init_db(engine: Engine) -> None:
    """SQLite: crea las tablas que falten. Postgres: solo revisa que ya
    existan, porque ahí las crea la migración (con RLS y la FK a
    auth.users, que `create_all` no pondría)."""
    if engine.dialect.name != "sqlite":
        missing = sorted(set(Base.metadata.tables) - set(inspect(engine).get_table_names()))
        if missing:
            logger.error("faltan tablas en la BD: %s", missing)
            raise SystemExit(f"ERROR: faltan las tablas {missing} en la BD. "
                             "Créalas con: alembic upgrade head")
        logger.info("tablas de la BD presentes")
        return
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
