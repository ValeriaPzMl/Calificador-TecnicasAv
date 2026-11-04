"""
Guardar una hoja calificada: la imagen de revisión en disco y el resultado
en la BD. Lo usan `cli.grade`, `cli.scan` y la API.

Las imágenes siguen en `data/processed/results/<examen>/` (también con
Supabase); pasarlas a Supabase Storage queda para después (`image_path`).
"""

import logging
import uuid
from pathlib import Path

import cv2
from sqlalchemy.orm import Session

from core.detection import draw_reading
from core.pipeline import GradedSheet

from . import repository

RESULTS_IMG_DIR = Path(__file__).resolve().parent.parent / "data" / "processed" / "results"

logger = logging.getLogger(__name__)


def save_graded(session: Session, graded: GradedSheet, stem: str, dry_run: bool,
                owner_id: uuid.UUID | None = None) -> tuple[str, Path, list]:
    """Guarda una hoja calificada: imagen de revisión y, si no es
    `dry_run`, el resultado en la BD a nombre de `owner_id` (por defecto,
    `default_owner_id()`).

    Devuelve (id del resultado, ruta de la imagen, otros resultados de la
    misma matrícula).
    """
    logger.info("inicio: guardando hoja de '%s' (dry_run=%s)", graded.exam.id, dry_run)
    correct = {q.number: q.correct_answer for s in graded.exam.sections for q in s.questions}
    review = draw_reading(graded.aligned, graded.layout, graded.reading, correct)

    out_dir = RESULTS_IMG_DIR / graded.exam.id
    out_dir.mkdir(parents=True, exist_ok=True)
    if dry_run:
        image_path = out_dir / f"{stem}_revision.png"
        cv2.imwrite(str(image_path), review)
        logger.info("fin: solo imagen de revisión (%s)", image_path)
        return "(no guardado)", image_path, []

    row, others = repository.add_result(session, graded.result, graded.reading, owner_id=owner_id)
    image_path = out_dir / f"{row.id}.png"
    cv2.imwrite(str(image_path), review)
    repository.set_image_path(session, row.id, str(image_path))
    logger.info("fin: resultado %s guardado, imagen en %s", row.id, image_path)
    return str(row.id), image_path, others
