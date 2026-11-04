"""
Califica fotos de hojas de respuestas y guarda los resultados en la BD.

Uso:
    python -m cli.grade data/raw/foto1.jpg
    python -m cli.grade data/raw/                 # todas las fotos de la carpeta
    python -m cli.grade foto.jpg --exam ejemplo0001   # si el QR no se lee
    python -m cli.grade foto.jpg --dry-run        # califica sin guardar
    python -m cli.grade foto.jpg -v               # ver el flujo

El examen se identifica por el QR de la hoja y debe estar registrado
(`python -m cli.exams add examen.json`). Por cada hoja se guarda una
imagen de revisión en data/processed/results/<examen>/:
verde = leída y correcta, rojo = leída e incorrecta,
naranja = ambigua (revisar), gris = en blanco.
"""

import argparse
import logging
from pathlib import Path

from cli.logs import add_verbose_flag, setup_logging
from core.alignment import AlignmentError
from core.layout import PAGE_SIZES_MM
from core.pipeline import GradedSheet, GradingError, grade_photo
from core.preprocessing import load_image
from storage import repository
from storage.db import open_session
from storage.saving import save_graded

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

logger = logging.getLogger(__name__)


def collect_photos(paths: list[str]) -> list[Path]:
    photos: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            found = sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_SUFFIXES)
            logger.info("carpeta %s: %d fotos", p, len(found))
            photos.extend(found)
        else:
            photos.append(p)
    logger.info("%d fotos por calificar", len(photos))
    return photos


def grade_one(session, path: Path, args) -> bool:
    logger.info("=== %s", path)
    try:
        image = load_image(path)
        graded = grade_photo(
            image,
            find_exam=lambda exam_id: repository.get_exam(session, exam_id),
            fallback_exam_id=args.exam,
            fallback_page=args.page,
        )
    except (FileNotFoundError, AlignmentError, GradingError) as exc:
        logger.error("no se pudo calificar %s: %s", path, type(exc).__name__)
        print(f"ERROR {path.name}: {exc}")
        return False

    result_id, image_path, others = save_graded(session, graded, path.stem, args.dry_run)
    print_result(path.name, graded, result_id, image_path, others)
    return True


def print_result(name: str, graded: GradedSheet, result_id: str, image_path: Path,
                 others: list) -> None:
    """Imprime el resumen de una hoja calificada."""
    result, reading = graded.result, graded.reading
    print(f"OK {name}: examen '{graded.exam.id}'"
          + ("" if graded.qr_read else " (QR no leído, se usó --exam)"))
    print(f"    Matrícula: {reading.student_id_raw}"
          + ("" if result.student_id else "  ← incompleta, REVISAR"))
    print(f"    Nota: {result.final_score:g} / {result.max_score:g}")
    for s in result.sections:
        weight = f", vale {s.weight:g}%" if s.weight is not None else ""
        print(f"      {s.name}: {s.points:g}/{s.max_points:g} ({s.percent:.0f}%{weight})")
    if result.ambiguous:
        print(f"    Ambiguas (valen 0, REVISAR): {result.ambiguous}")
    if result.blank:
        print(f"    En blanco: {result.blank}")
    if others:
        print(f"    AVISO: la matrícula {result.student_id} ya tenía {len(others)} "
              "resultado(s). Se guardaron todos; elige cuál cuenta con:")
        print(f"      python -m cli.results duplicates {graded.exam.id}")
    print(f"    Resultado: {result_id}")
    print(f"    Revisión: {image_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("paths", nargs="+", help="Fotos o carpetas con fotos")
    parser.add_argument("--exam", help="Id del examen, por si el QR no se puede leer")
    parser.add_argument("--page", default="letter", choices=list(PAGE_SIZES_MM),
                        help="Tamaño de página, solo si se usa --exam sin QR")
    parser.add_argument("--dry-run", action="store_true", help="Calificar sin guardar en la BD")
    add_verbose_flag(parser)
    args = parser.parse_args()
    setup_logging(args.verbose)
    logger.info("inicio: paths=%s exam=%s dry_run=%s", args.paths, args.exam, args.dry_run)

    session = open_session()
    photos = collect_photos(args.paths)
    ok = sum(grade_one(session, p, args) for p in photos)
    if len(photos) > 1:
        print(f"\n{ok} de {len(photos)} hojas calificadas.")
    logger.info("fin: %d/%d calificadas", ok, len(photos))
    if ok < len(photos):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
