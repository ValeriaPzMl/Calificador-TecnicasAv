"""
Escaneo en vivo: recibe cuadros de la cámara, uno por uno, y decide cuándo
ya tiene una lectura confiable de la hoja.

No sabe nada de cámaras ni de ventanas: solo recibe imágenes. Lo usan
`cli/scan.py` ahora (webcam + ventana de OpenCV) y la API en la fase web
(el navegador manda los cuadros). Igual que `pipeline.py`, no depende de la
BD: quien lo crea pasa `find_exam(exam_id)`.

Por cada cuadro (`LiveScanner.process`):
1. Se reduce a MAX_SIDE px (velocidad).
2. Marcadores. Si faltan → "buscando hoja" (con los que sí se ven).
3. QR → examen → layout, solo hasta saber el examen; luego se reutilizan.
4. Se endereza y se mide la nitidez. Muy borrosa (cámara en movimiento)
   → se descarta el cuadro.
5. Se lee la hoja (`read_sheet`).
6. Estabilidad: la lectura (matrícula, respuestas, ambiguas y en blanco)
   tiene que salir **idéntica en STABLE_FRAMES cuadros seguidos**. Si
   cambia, la cuenta vuelve a empezar. Así una mano pasando o un cuadro
   movido no producen una lectura falsa.
7. Al ser estable → se califica y el estado pasa a "lista". De la racha se
   guarda el cuadro más nítido como evidencia.

Las preguntas ambiguas o la matrícula incompleta no impiden terminar: si
salen igual en varios cuadros son la lectura real, y quedan marcadas para
revisión igual que con una foto. Nunca se adivina.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

import cv2
import numpy as np

from models.exam import Exam
from models.result import SheetReading

from .alignment import AlignmentError, align_sheet, detect_corner_markers
from .detection import read_sheet
from .layout import SheetLayout, build_layout
from .pipeline import GradedSheet
from .preprocessing import to_grayscale
from .qr import read_qr
from .scoring import score

logger = logging.getLogger(__name__)

MAX_SIDE = 1280  # lado mayor (px) al que se reduce cada cuadro
STABLE_FRAMES = 3  # cuadros seguidos con la misma lectura para darla por buena
# Varianza del Laplaciano de la hoja enderezada. Fotos nítidas de celular:
# 84-604; con desenfoque leve: 9-36. Es permisivo a propósito: una webcam de
# baja resolución da valores bajos, y la lectura aguanta algo de desenfoque.
MIN_SHARPNESS = 15.0
QR_TRIES_BEFORE_FALLBACK = 10  # cuadros sin leer el QR antes de usar --exam

# Estados
SEARCHING = "buscando_hoja"
NO_QR = "sin_qr"
UNKNOWN_EXAM = "examen_desconocido"
BLURRY = "borrosa"
READING = "leyendo"
READY = "lista"


@dataclass
class FrameStatus:
    state: str
    message: str  # para mostrarle al usuario
    corners: dict[str, tuple[float, float]] = field(default_factory=dict)  # px del cuadro original
    progress: int = 0  # cuadros seguidos con la misma lectura
    needed: int = STABLE_FRAMES
    exam_id: str | None = None
    reading: SheetReading | None = None  # lectura de este cuadro
    graded: GradedSheet | None = None  # solo cuando state == READY
    capture: np.ndarray | None = None  # cuadro original elegido, solo con READY


@dataclass
class _Candidate:
    sharpness: float
    frame: np.ndarray
    aligned: np.ndarray
    reading: SheetReading


class LiveScanner:
    """Un escaneo de una hoja. Para la siguiente hoja se crea uno nuevo."""

    def __init__(self, find_exam: Callable[[str], Exam | None],
                 fallback_exam_id: str | None = None, fallback_page: str = "letter"):
        logger.info("inicio: escaneo en vivo (examen de respaldo: %s)", fallback_exam_id)
        self.find_exam = find_exam
        self.fallback_exam_id = fallback_exam_id
        self.fallback_page = fallback_page
        self.exam: Exam | None = None
        self.layout: SheetLayout | None = None
        self.qr_read = False
        self._qr_misses = 0
        self._streak: list[_Candidate] = []
        self._streak_key: tuple | None = None
        self._done: FrameStatus | None = None
        self._last_state: str | None = None
        self.frames = 0

    def process(self, frame: np.ndarray) -> FrameStatus:
        """Procesa un cuadro y dice en qué va el escaneo."""
        if self._done is not None:
            return self._done
        self.frames += 1
        status = self._process(frame)
        if status.state != self._last_state:
            logger.info("cuadro %d: %s — %s", self.frames, status.state, status.message)
            self._last_state = status.state
        else:
            logger.debug("cuadro %d: %s — %s", self.frames, status.state, status.message)
        if status.state == READY:
            self._done = status
        return status

    def _process(self, frame: np.ndarray) -> FrameStatus:
        small, scale = _shrink(frame)

        def to_frame(points: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
            return {k: (x / scale, y / scale) for k, (x, y) in points.items()}

        try:
            markers = detect_corner_markers(small)
        except AlignmentError as exc:
            self._reset_streak()
            seen = len(exc.found)
            return FrameStatus(SEARCHING, f"Buscando la hoja: se ven {seen} de 4 esquinas",
                               corners=to_frame(exc.found), exam_id=self._exam_id())
        corners = to_frame(markers.centers)

        if self.exam is None:
            status = self._identify_exam(small, markers, corners)
            if status is not None:
                return status

        aligned = align_sheet(small, markers, size=self.layout.canonical_size)
        sharpness = cv2.Laplacian(to_grayscale(aligned), cv2.CV_64F).var()
        logger.debug("nitidez %.1f", sharpness)
        if sharpness < MIN_SHARPNESS:
            self._reset_streak()
            return FrameStatus(BLURRY, "Imagen borrosa: deja quieta la cámara",
                               corners=corners, exam_id=self.exam.id)

        reading = read_sheet(aligned, self.layout, exam_id=self.exam.id)
        key = (reading.student_id_raw, tuple(reading.answers.items()),
               tuple(reading.ambiguous), tuple(reading.blank))
        if key != self._streak_key:
            if self._streak_key is not None:
                logger.debug("la lectura cambió; la cuenta vuelve a empezar")
            self._streak_key, self._streak = key, []
        self._streak.append(_Candidate(sharpness, frame, aligned, reading))
        progress = len(self._streak)

        if progress < STABLE_FRAMES:
            return FrameStatus(READING, f"Leyendo… ({progress}/{STABLE_FRAMES}) no muevas la hoja",
                               corners=corners, progress=progress, exam_id=self.exam.id,
                               reading=reading)

        best = max(self._streak, key=lambda c: c.sharpness)
        result = score(self.exam, best.reading)
        graded = GradedSheet(self.exam, self.layout, best.aligned, best.reading, result,
                             self.qr_read)
        logger.info("fin: lectura estable en %d cuadros (de %d procesados): matrícula %s, "
                    "nota %g/%g", progress, self.frames, best.reading.student_id_raw,
                    result.final_score, result.max_score)
        return FrameStatus(READY, "¡Lista!", corners=corners, progress=progress,
                           exam_id=self.exam.id, reading=best.reading, graded=graded,
                           capture=best.frame)

    def _identify_exam(self, small, markers, corners) -> FrameStatus | None:
        """Lee el QR y prepara examen y layout. Devuelve un estado si todavía
        no se puede seguir, o None si ya se sabe el examen."""
        qr = read_qr(small, markers)
        if qr is not None:
            exam_id, page, self.qr_read = qr.exam_id, qr.page, True
        else:
            self._qr_misses += 1
            if self.fallback_exam_id and self._qr_misses >= QR_TRIES_BEFORE_FALLBACK:
                logger.warning("QR sin leer en %d cuadros; se usa el examen indicado '%s'",
                               self._qr_misses, self.fallback_exam_id)
                exam_id, page, self.qr_read = self.fallback_exam_id, self.fallback_page, False
            else:
                return FrameStatus(NO_QR, "Acerca la cámara: no se lee el código QR",
                                   corners=corners)

        exam = self.find_exam(exam_id)
        if exam is None:
            return FrameStatus(UNKNOWN_EXAM, f"El examen '{exam_id}' no está registrado",
                               corners=corners, exam_id=exam_id)
        self.exam, self.layout = exam, build_layout(exam, page=page)
        logger.info("examen identificado: '%s' (%s), página %s", exam.id, exam.name, page)
        return None

    def _exam_id(self) -> str | None:
        return self.exam.id if self.exam else None

    def _reset_streak(self) -> None:
        if self._streak:
            logger.debug("se pierde la racha de %d cuadros", len(self._streak))
        self._streak, self._streak_key = [], None


def _shrink(frame: np.ndarray) -> tuple[np.ndarray, float]:
    """Reduce el cuadro a MAX_SIDE px de lado mayor. Devuelve (imagen, escala)."""
    h, w = frame.shape[:2]
    scale = MAX_SIDE / max(h, w)
    if scale >= 1:
        return frame, 1.0
    small = cv2.resize(frame, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    return small, scale
