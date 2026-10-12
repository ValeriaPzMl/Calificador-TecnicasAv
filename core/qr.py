"""
Lectura del QR que identifica al examen.

El QR se lee **antes** de saber qué examen es (es justamente lo que lo
dice), así que aquí no se usa el layout del examen sino solo la geometría
de página (`layout.page_geometry`), que es igual para todos los exámenes.

En vez de buscar el QR en toda la foto, se endereza solo su región a alta
resolución usando los marcadores ArUco ya detectados. Esto lo hace mucho
más robusto con fotos de celular borrosas o con ruido.
"""

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from .alignment import DetectedMarkers, detect_corner_markers, warp_region
from .layout import PAGE_SIZES_MM, QR_PREFIX, page_geometry
from .preprocessing import to_grayscale

QR_PX_PER_MM = 8.0
QR_PADDING_MM = 3.0  # margen alrededor del QR al recortar

_detector = cv2.QRCodeDetector()

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QrReading:
    exam_id: str
    page: str  # tamaño de página con el que se encontró el QR ("letter", "a4")


def read_exam_id(image: np.ndarray, markers: DetectedMarkers | None = None) -> str | None:
    """Devuelve el id del examen leído del QR, o None si no se pudo leer."""
    qr = read_qr(image, markers)
    return qr.exam_id if qr else None


def read_qr(image: np.ndarray, markers: DetectedMarkers | None = None) -> QrReading | None:
    """Lee el QR y dice de qué examen es y en qué tamaño de página se
    imprimió (el layout lo necesita), o None si no se pudo leer.

    Prueba cada tamaño de página conocido, porque la posición del QR en mm
    depende de él. Lanza AlignmentError si no se encuentran los marcadores.
    """
    logger.info("inicio: leyendo el QR del examen")
    if markers is None:
        logger.debug("no se pasaron marcadores, se detectan aquí")
        markers = detect_corner_markers(image)

    for page in PAGE_SIZES_MM:
        logger.debug("probando posición del QR para página '%s'", page)
        geo = page_geometry(page)
        box = geo.qr_box
        region = (
            box.x - QR_PADDING_MM,
            box.y - QR_PADDING_MM,
            box.w + 2 * QR_PADDING_MM,
            box.h + 2 * QR_PADDING_MM,
        )
        crop = to_grayscale(
            warp_region(image, markers, geo.marker_centers, region, QR_PX_PER_MM)
        )
        payload = _decode(crop)
        if payload and payload.startswith(QR_PREFIX):
            exam_id = payload[len(QR_PREFIX):]
            logger.info("fin: QR leído en página '%s', examen '%s'", page, exam_id)
            return QrReading(exam_id, page)
        if payload:
            logger.debug("QR con contenido ajeno (sin prefijo %s): %r", QR_PREFIX, payload)
    logger.warning("fin: no se pudo leer el QR con ningún tamaño de página")
    return None


def _decode(gray: np.ndarray) -> str:
    """Intenta decodificar tal cual y, si falla, con la imagen binarizada
    (ayuda cuando hay ruido o iluminación despareja)."""
    payload, _, _ = _detector.detectAndDecode(gray)
    if payload:
        logger.debug("decodificado directo: %r", payload)
        return payload
    logger.debug("no se leyó directo, reintentando binarizado (Otsu)")
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    payload, _, _ = _detector.detectAndDecode(binary)
    logger.debug("binarizado: %r", payload or "sin lectura")
    return payload
