"""
Guardar una hoja calificada: el resultado en la BD y su imagen de revisión
en el almacén de imágenes (`storage/images.py`: Supabase Storage o disco).
Lo usan `cli.grade`, `cli.scan` y la API.

Con `dry_run` no se guarda nada en la BD y la imagen de revisión solo se
escribe en disco (`data/processed/results/<examen>/`), para verla.
"""

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy.orm import Session

from core.detection import draw_reading
from core.pipeline import GradedSheet

from . import repository
from .images import (LOCAL_DIR, ImageStoreError, LocalImageStore, capture_key,
                     get_image_store, image_key)

RESULTS_IMG_DIR = LOCAL_DIR  # imágenes de las pruebas sin guardar (dry_run)
JPEG_QUALITY = 90

logger = logging.getLogger(__name__)


@dataclass
class SavedSheet:
    result_id: str | None  # None si fue dry_run
    review: np.ndarray  # la imagen de revisión (BGR)
    review_jpg: bytes
    location: str  # dónde quedó la imagen (ruta en disco o bucket/llave)
    others: list = field(default_factory=list)  # otras hojas de la misma matrícula


def save_graded(session: Session, graded: GradedSheet, stem: str, dry_run: bool,
                owner_id: uuid.UUID | None = None, capture: np.ndarray | None = None,
                images=None) -> SavedSheet:
    """Guarda una hoja calificada a nombre de `owner_id` (por defecto,
    `default_owner_id()`). `capture` es el cuadro de la cámara en vivo; se
    guarda solo en disco (sirve para depurar). Si la imagen no se puede
    subir, el resultado queda guardado sin imagen."""
    logger.info("inicio: guardando hoja de '%s' (dry_run=%s)", graded.exam.id, dry_run)
    images = images or get_image_store()
    correct = {q.number: q.correct_answer for s in graded.exam.sections for q in s.questions}
    review = draw_reading(graded.aligned, graded.layout, graded.reading, correct)
    review_jpg = _jpeg(review)

    if dry_run:
        out_dir = RESULTS_IMG_DIR / graded.exam.id
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{stem}_revision.jpg"
        path.write_bytes(review_jpg)
        if capture is not None:
            cv2.imwrite(str(out_dir / f"{stem}_captura.jpg"), capture)
        logger.info("fin: solo imagen de revisión (%s)", path)
        return SavedSheet(None, review, review_jpg, str(path))

    row, others = repository.add_result(session, graded.result, graded.reading, owner_id=owner_id)
    key = image_key(row.owner_id, row.exam_id, row.id)
    try:
        images.put(key, review_jpg)
    except ImageStoreError as exc:
        logger.error("resultado %s guardado SIN imagen de revisión: %s", row.id, exc)
        return SavedSheet(str(row.id), review, review_jpg, "(no se pudo guardar la imagen)",
                          others)
    repository.set_image_path(session, row.id, key)
    if capture is not None and isinstance(images, LocalImageStore):
        images.put(capture_key(key, row.id), _jpeg(capture))
    location = (str(images.path(key)) if isinstance(images, LocalImageStore)
                else f"{images.kind}: {images.bucket}/{key}")
    logger.info("fin: resultado %s guardado, imagen en %s", row.id, location)
    return SavedSheet(str(row.id), review, review_jpg, location, others)


def delete_saved(session: Session, row, images=None) -> None:
    """Borra una hoja guardada: el resultado de la BD y sus imágenes."""
    images = images or get_image_store()
    keys = _image_keys([row])
    repository.delete_result(session, row)
    _delete_images(images, keys)


def delete_exam_saved(session: Session, exam_id: str, owner_id: uuid.UUID | None = None,
                      images=None) -> int:
    """Borra un examen con sus resultados (como `repository.delete_exam`) y
    las imágenes de esos resultados. Regresa cuántos resultados se borraron."""
    images = images or get_image_store()
    keys = _image_keys(repository.list_results(session, exam_id))
    deleted = repository.delete_exam(session, exam_id, owner_id=owner_id)
    _delete_images(images, keys)
    return deleted


def review_jpeg(data: bytes | None, max_side: int) -> bytes | None:
    """La imagen de revisión reducida a `max_side` px (para el navegador)."""
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
    if image is None:
        logger.warning("no se pudo leer la imagen de revisión")
        return None
    h, w = image.shape[:2]
    scale = max_side / max(h, w)
    if scale < 1:
        image = cv2.resize(image, (round(w * scale), round(h * scale)),
                           interpolation=cv2.INTER_AREA)
    logger.debug("imagen de revisión de %dx%d px", image.shape[1], image.shape[0])
    return _jpeg(image, quality=85)


def _jpeg(image: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    ok, jpg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("no se pudo codificar la imagen a JPEG")
    return jpg.tobytes()


def _image_keys(rows) -> list[str]:
    keys = []
    for row in rows:
        if row.image_path:
            keys += [row.image_path, capture_key(row.image_path, row.id)]
    return keys


def _delete_images(images, keys: list[str]) -> None:
    """Borrar imágenes es lo último y no debe fallar el borrado: si Storage
    no responde, queda en el log."""
    try:
        images.delete(keys)
    except ImageStoreError as exc:
        logger.error("no se pudieron borrar %d imagen(es): %s", len(keys), exc)
