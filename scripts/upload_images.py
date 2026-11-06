"""
Sube a Supabase Storage las imágenes de revisión que quedaron en disco (las
hojas calificadas antes de configurar Storage) y actualiza `image_path`.

Uso (desde Calificador/, con SUPABASE_URL y SUPABASE_SECRET_KEY en .env):
    python scripts/upload_images.py --dry-run    # solo cuenta
    python scripts/upload_images.py
    python scripts/upload_images.py --delete-local   # y borra las de disco ya subidas

- Usa la BD de DATABASE_URL (la de Supabase).
- Solo toca resultados cuya imagen es una ruta en disco; las que ya están en
  Storage se saltan, así que se puede correr otra vez.
- Las PNG se convierten a JPEG (pesan mucho menos).
- Si un archivo ya no existe en disco, el resultado queda sin imagen.
"""

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from cli.logs import add_verbose_flag, setup_logging  # noqa: E402
from storage.db import init_db, make_engine  # noqa: E402
from storage.images import SupabaseImageStore, image_key, image_store_from_env  # noqa: E402
from storage.saving import JPEG_QUALITY  # noqa: E402
from storage.tables import ResultRow  # noqa: E402

logger = logging.getLogger("upload_images")


def to_jpeg(path: Path) -> bytes | None:
    image = cv2.imread(str(path))
    if image is None:
        return None
    ok, jpg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return jpg.tobytes() if ok else None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="Solo contar, sin subir")
    parser.add_argument("--delete-local", action="store_true",
                        help="Borrar de disco las imágenes ya subidas")
    add_verbose_flag(parser)
    args = parser.parse_args()
    setup_logging(args.verbose)
    logger.info("inicio: dry_run=%s delete_local=%s", args.dry_run, args.delete_local)

    store = image_store_from_env()
    if not isinstance(store, SupabaseImageStore):
        raise SystemExit("ERROR: falta SUPABASE_URL o SUPABASE_SECRET_KEY en .env")
    if not args.dry_run:
        store.ensure_ready()

    engine = make_engine()
    init_db(engine)
    counts = {"subidas": 0, "ya en Storage": 0, "sin archivo": 0}
    with Session(engine) as session:
        for row in session.scalars(select(ResultRow).where(ResultRow.image_path.is_not(None))):
            local = Path(row.image_path)
            if not local.is_absolute():
                counts["ya en Storage"] += 1
                continue
            data = to_jpeg(local)
            if data is None:
                logger.warning("resultado %s: no existe %s; queda sin imagen", row.id, local)
                counts["sin archivo"] += 1
                if not args.dry_run:
                    row.image_path = None
                    session.commit()
                continue
            key = image_key(row.owner_id, row.exam_id, row.id)
            counts["subidas"] += 1
            if args.dry_run:
                logger.info("subiría %s -> %s", local, key)
                continue
            store.put(key, data)
            row.image_path = key
            session.commit()  # una por una: si se corta, lo subido queda anotado
            if args.delete_local:
                local.unlink()
                logger.debug("borrada de disco: %s", local)
    verb = "Se subirían" if args.dry_run else "Listo"
    print(f"{verb}: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    logger.info("fin: %s", counts)


if __name__ == "__main__":
    main()
