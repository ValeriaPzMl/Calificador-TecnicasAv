"""
Flujo completo de una foto: foto -> marcadores -> QR -> examen -> layout
-> hoja alineada -> lectura -> nota.

Lo usan `cli/grade.py` ahora y la API (`POST /scan`) en la fase 4. No sabe
de dónde vienen los exámenes: quien llama pasa `find_exam(exam_id)`, que
puede buscar en la BD o donde sea. Así `core/` sigue sin depender de
`storage/`.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from models.exam import Exam
from models.result import Result, SheetReading

from .alignment import align_sheet, detect_corner_markers
from .detection import read_sheet
from .layout import SheetLayout, build_layout
from .qr import read_qr
from .scoring import score

logger = logging.getLogger(__name__)


class GradingError(Exception):
    """La foto no se pudo calificar (falta el QR, el examen no existe...)."""


@dataclass
class GradedSheet:
    exam: Exam
    layout: SheetLayout
    aligned: np.ndarray
    reading: SheetReading
    result: Result
    qr_read: bool  # False si se usó el examen de respaldo porque no se leyó el QR


def grade_photo(
    image: np.ndarray,
    find_exam: Callable[[str], Exam | None],
    fallback_exam_id: str | None = None,
    fallback_page: str = "letter",
) -> GradedSheet:
    """Califica una foto. Lanza AlignmentError si no se ven los 4
    marcadores y GradingError si no se sabe o no existe el examen.

    `fallback_exam_id` se usa solo si el QR no se puede leer.
    """
    logger.info("inicio: calificando foto de %dx%d px", image.shape[1], image.shape[0])

    logger.info("paso 1/5: marcadores")
    markers = detect_corner_markers(image)

    logger.info("paso 2/5: QR")
    qr = read_qr(image, markers)
    if qr is not None:
        exam_id, page, qr_read = qr.exam_id, qr.page, True
        if fallback_exam_id and fallback_exam_id != exam_id:
            logger.warning("se indicó el examen '%s' pero el QR dice '%s'; se usa el del QR",
                           fallback_exam_id, exam_id)
    elif fallback_exam_id:
        logger.warning("no se leyó el QR; se usa el examen indicado '%s' (página %s)",
                       fallback_exam_id, fallback_page)
        exam_id, page, qr_read = fallback_exam_id, fallback_page, False
    else:
        raise GradingError(
            "No se pudo leer el QR de la hoja. Revisa que se vea completo y "
            "nítido, o indica el examen a mano."
        )

    logger.info("paso 3/5: buscando examen '%s'", exam_id)
    exam = find_exam(exam_id)
    if exam is None:
        raise GradingError(
            f"La hoja es del examen '{exam_id}', pero ese examen no está registrado."
        )
    layout = build_layout(exam, page=page)

    logger.info("paso 4/5: alineando y leyendo burbujas")
    aligned = align_sheet(image, markers, size=layout.canonical_size)
    reading = read_sheet(aligned, layout, exam_id=exam.id)

    logger.info("paso 5/5: calificando")
    result = score(exam, reading)

    logger.info("fin: examen '%s', matrícula %s, nota %g/%g",
                exam.id, reading.student_id_raw, result.final_score, result.max_score)
    return GradedSheet(exam, layout, aligned, reading, result, qr_read)
