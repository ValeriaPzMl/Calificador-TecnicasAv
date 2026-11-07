"""
La app ya publicada en internet (Render): `uvicorn api.app:app`.

A diferencia de `api.serve` (la red de tu casa, con certificado propio),
aquí el HTTPS lo pone Render y todo se configura con variables de entorno
(las mismas del `.env`, puestas en el panel de Render):

    DATABASE_URL              Postgres de Supabase (Session pooler)
    SUPABASE_URL              https://<ref>.supabase.co
    SUPABASE_PUBLISHABLE_KEY  la que ve el navegador
    SUPABASE_SECRET_KEY       solo servidor: imágenes en Storage
    LOG_LEVEL                 INFO (por defecto), DEBUG o WARNING

Se niega a arrancar si falta algo de eso: sin Supabase la app quedaría en
"modo local" (sin login, todo de un mismo usuario) y abierta a cualquiera,
y con SQLite los datos se borrarían en cada despliegue.
"""

import logging
import os

from sqlalchemy.orm import sessionmaker

from api.auth import Auth
from api.main import WEB_DIST, create_app
from cli.logs import DATE_FORMAT, LOG_FORMAT
from storage.db import database_url, init_db, make_engine
from storage.images import SupabaseImageStore, image_store_from_env

REQUIRED = ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SECRET_KEY")

logger = logging.getLogger("api.app")


def setup_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format=LOG_FORMAT, datefmt=DATE_FORMAT, force=True)
    # Lo de core/ por cada cuadro de la cámara es demasiado; solo avisos
    if level != "DEBUG":
        logging.getLogger("core").setLevel(logging.WARNING)
        logging.getLogger("core.live").setLevel(logging.INFO)
        logging.getLogger("core.pipeline").setLevel(logging.INFO)
    for noisy in ("PIL", "reportlab", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def build_app():
    setup_logging()
    logger.info("inicio: arrancando la app publicada")
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"ERROR: faltan variables de entorno: {', '.join(missing)}")
    if not database_url().startswith("postgresql"):
        raise SystemExit("ERROR: DATABASE_URL debe ser el Postgres de Supabase.")

    engine = make_engine()
    init_db(engine)  # en Postgres solo revisa que existan las tablas
    images = image_store_from_env()
    if not isinstance(images, SupabaseImageStore):
        raise SystemExit("ERROR: las imágenes deben ir a Supabase Storage.")
    images.ensure_ready()
    auth = Auth.from_env()
    if not (WEB_DIST / "index.html").exists():
        logger.error("no está la web compilada en %s", WEB_DIST)
    app = create_app(sessionmaker(engine), auth, images=images)
    logger.info("fin: app lista (web en %s)", WEB_DIST)
    return app


app = build_app()
