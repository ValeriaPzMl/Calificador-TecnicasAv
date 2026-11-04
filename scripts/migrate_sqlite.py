"""
Copia los exámenes y resultados de la BD local (SQLite) a Supabase, a
nombre de un usuario (p. ej. el admin).

Uso (desde Calificador/, con DATABASE_URL de Supabase en .env):
    python scripts/migrate_sqlite.py --owner admin@correo.com --dry-run   # solo cuenta
    python scripts/migrate_sqlite.py --owner admin@correo.com
    python scripts/migrate_sqlite.py --owner <uuid del usuario> -v

- `--owner` es el correo o el uuid del usuario en Supabase
  (Authentication → Users). Tiene que existir antes.
- Se copian con los mismos ids (el id del examen va en el QR de las hojas
  ya impresas, así que siguen sirviendo).
- Lo que ya exista en Supabase se salta: se puede correr otra vez sin
  duplicar.
- No borra ni cambia la BD local.
- Las imágenes de revisión siguen en disco (`image_path` se copia igual).
"""

import argparse
import logging
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from cli.logs import add_verbose_flag, setup_logging  # noqa: E402
from storage.db import DEFAULT_DB_PATH, database_url, init_db, make_engine  # noqa: E402
from storage.tables import ExamRow, QuestionRow, ResultRow, SectionRow  # noqa: E402

logger = logging.getLogger("migrate_sqlite")


def resolve_owner(target: Session, owner: str) -> uuid.UUID:
    """Correo o uuid -> uuid del usuario en auth.users (debe existir)."""
    column = "email" if "@" in owner else "id"
    row = target.execute(text(f"select id, email from auth.users where {column} = :v"),
                         {"v": owner if column == "email" else str(uuid.UUID(owner))}).first()
    if row is None:
        raise SystemExit(f"ERROR: no existe el usuario {owner!r} en Supabase "
                         "(créalo en Authentication → Users)")
    logger.info("usuario destino: %s (%s)", row.email, row.id)
    return uuid.UUID(str(row.id))


def copy_rows(source: Session, target: Session, model, owner_id: uuid.UUID, dry_run: bool,
              parent: tuple[str, set] | None = None) -> tuple[set, int]:
    """Copia las filas de una tabla que no existan ya en el destino.

    `parent` = (columna, ids copiados): solo se copian las filas cuyo padre
    también se copió. Si un examen ya existía en Supabase (aunque sea de
    otro usuario), no se le agregan secciones ni resultados.
    Regresa (ids copiados, cuántas se saltaron)."""
    columns = [c.key for c in model.__table__.columns]
    existing = set(target.scalars(select(model.id)))
    copied: set = set()
    skipped = 0
    for row in source.scalars(select(model)):
        if row.id in existing:
            logger.debug("%s %s ya existe: se salta", model.__tablename__, row.id)
            skipped += 1
            continue
        if parent is not None and getattr(row, parent[0]) not in parent[1]:
            logger.debug("%s %s: su %s no se copió, se salta", model.__tablename__, row.id,
                         parent[0])
            skipped += 1
            continue
        values = {c: getattr(row, c) for c in columns}
        if "owner_id" in values:
            values["owner_id"] = owner_id
        logger.debug("%s %s: se copia", model.__tablename__, row.id)
        if not dry_run:
            target.add(model(**values))
        copied.add(row.id)
    if not dry_run:
        target.flush()  # antes de la siguiente tabla, por las FK
    logger.info("%s: %d por copiar, %d saltadas", model.__tablename__, len(copied), skipped)
    return copied, skipped


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--owner", required=True, help="Correo o uuid del usuario en Supabase")
    parser.add_argument("--source", default=str(DEFAULT_DB_PATH), help="BD SQLite de origen")
    parser.add_argument("--dry-run", action="store_true", help="Solo contar, sin copiar")
    add_verbose_flag(parser)
    args = parser.parse_args()
    setup_logging(args.verbose)
    logger.info("inicio: source=%s owner=%s dry_run=%s", args.source, args.owner, args.dry_run)

    if not database_url().startswith("postgresql"):
        raise SystemExit("ERROR: DATABASE_URL no apunta a Supabase/Postgres (revisa el .env)")
    if not Path(args.source).exists():
        raise SystemExit(f"ERROR: no existe la BD local {args.source}")

    target_engine = make_engine()
    init_db(target_engine)  # en Postgres solo revisa que estén las tablas
    with Session(make_engine(f"sqlite:///{args.source}")) as source, \
            Session(target_engine) as target:
        owner_id = resolve_owner(target, args.owner)
        # En orden, por las FK: examen -> secciones -> preguntas; resultados al final
        exams = copy_rows(source, target, ExamRow, owner_id, args.dry_run)
        sections = copy_rows(source, target, SectionRow, owner_id, args.dry_run,
                             parent=("exam_id", exams[0]))
        questions = copy_rows(source, target, QuestionRow, owner_id, args.dry_run,
                              parent=("section_id", sections[0]))
        results = copy_rows(source, target, ResultRow, owner_id, args.dry_run,
                            parent=("exam_id", exams[0]))
        totals = {"exámenes": exams, "secciones": sections, "preguntas": questions,
                  "resultados": results}
        if args.dry_run:
            target.rollback()
        else:
            target.commit()

    verb = "Se copiarían" if args.dry_run else "Copiados"
    for table, (copied, skipped) in totals.items():
        print(f"{verb} {len(copied):4d} {table}"
              + (f"  ({skipped} se saltaron: ya estaban o su examen ya estaba)" if skipped else ""))
    if args.dry_run:
        print("\nNo se copió nada (--dry-run).")
    logger.info("fin: %s", {t: len(c) for t, (c, _) in totals.items()})


if __name__ == "__main__":
    main()
