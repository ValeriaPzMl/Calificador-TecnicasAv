"""
Alineación de la hoja de respuestas usando marcadores ArUco.

Idea: la plantilla impresa lleva 4 marcadores ArUco, uno en cada esquina,
con IDs fijos (ver `CORNER_IDS`). Sin importar el ángulo, la rotación o el
recorte de la foto, mientras se vean los 4 marcadores podemos calcular una
transformación de perspectiva que "aplana" la hoja a un tamaño canónico
fijo. A partir de ahí, todas las coordenadas de las burbujas son conocidas
de antemano (se definen al diseñar la plantilla), y `detection.py` ya no
tiene que adivinar dónde está cada cosa.

Por qué ArUco y no buscar 4 cuadros negros por contornos: detectar
marcadores es una sola llamada de OpenCV, ya resuelta y robusta a
iluminación pareja/dispareja, rotación y oclusión parcial. Contornos a mano
es más frágil y es más código para mantener.
"""

import logging
from dataclasses import dataclass

import cv2
import numpy as np

# IDs de los marcadores según la esquina donde van pegados en la plantilla.
# Genera las imágenes correspondientes con scripts/generate_markers.py
CORNER_IDS = {
    "top_left": 0,
    "top_right": 1,
    "bottom_right": 2,
    "bottom_left": 3,
}

# Tamaño por defecto de la hoja "aplanada", para fotos de hojas que no
# fueron generadas por `layout.py`. Con una hoja generada usa
# `SheetLayout.canonical_size`, que respeta la proporción real entre los
# centros de los marcadores.
CANONICAL_SIZE = (1000, 1400)  # (ancho, alto) en píxeles

ARUCO_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
_ARUCO_PARAMS = cv2.aruco.DetectorParameters()

# Lado mayor (px) de las versiones reducidas con que se reintenta si falta
# algún marcador. Con fotos reales de 4624 px, un marcador mal impreso solo
# se detectó al reducir a ~2000.
RETRY_LONG_SIDES = (2000, 1400, 1000)

logger = logging.getLogger(__name__)


class AlignmentError(Exception):
    """No se pudieron encontrar los 4 marcadores de esquina en la imagen.

    `found` tiene los que sí se encontraron ({esquina: (x, y)}), para que
    el escaneo en vivo pueda dibujarlos y orientar al usuario.
    """

    def __init__(self, message: str, found: dict[str, tuple[float, float]] | None = None):
        super().__init__(message)
        self.found = found or {}


@dataclass
class DetectedMarkers:
    centers: dict[str, tuple[float, float]]  # nombre de esquina -> (x, y)


def detect_corner_markers(image: np.ndarray) -> DetectedMarkers:
    """Busca los 4 marcadores ArUco de esquina en la imagen original (BGR).

    Devuelve el centro de cada marcador encontrado, indexado por nombre de
    esquina ("top_left", etc). Lanza AlignmentError si falta alguno —
    preferible fallar aquí con un mensaje claro a que el warp de perspectiva
    produzca una imagen deformada sin avisar.

    Primero busca a resolución completa (centros más precisos). Si falta
    alguno, reintenta con la imagen reducida (RETRY_LONG_SIDES): en fotos
    de celular grandes, un marcador mal impreso (rayado, tóner bajo) tiene
    textura que confunde al detector, y al reducir se promedia y vuelve a
    verse sólido. Solo se toman de ahí los marcadores que faltaban.
    """
    h, w = image.shape[:2]
    logger.info("inicio: buscando marcadores ArUco en imagen de %dx%d px", w, h)

    id_to_center = _detect_ids(image, scale=1.0)
    for long_side in RETRY_LONG_SIDES:
        if all(i in id_to_center for i in CORNER_IDS.values()):
            break
        scale = long_side / max(h, w)
        if scale >= 1:
            continue
        missing_ids = sorted(set(CORNER_IDS.values()) - set(id_to_center))
        logger.info("faltan ids %s; reintentando con la imagen reducida a %d px de lado",
                    missing_ids, long_side)
        small = cv2.resize(image, (round(w * scale), round(h * scale)),
                           interpolation=cv2.INTER_AREA)
        for marker_id, center in _detect_ids(small, scale).items():
            if marker_id not in id_to_center:
                logger.info("marcador id=%d recuperado a %d px", marker_id, long_side)
                id_to_center[marker_id] = center

    if not id_to_center:
        logger.warning("no se detectó ningún marcador")
        raise AlignmentError(
            "No se detectó ningún marcador ArUco en la imagen. "
            "Revisa iluminación, enfoque, y que los 4 marcadores estén visibles."
        )

    centers: dict[str, tuple[float, float]] = {}
    missing = []
    for corner_name, marker_id in CORNER_IDS.items():
        if marker_id in id_to_center:
            centers[corner_name] = id_to_center[marker_id]
        else:
            missing.append(f"{corner_name} (id={marker_id})")

    if missing:
        logger.warning("faltan marcadores: %s (detectados: %s)", missing, sorted(id_to_center))
        raise AlignmentError(
            "Faltan marcadores de esquina: " + ", ".join(missing)
            + ". Revisa que se vean completos, sin reflejos ni sombras, y que "
            "estén bien impresos (negro sólido).",
            found=centers,
        )

    logger.info("fin: los 4 marcadores de esquina encontrados")
    return DetectedMarkers(centers=centers)


def _detect_ids(image: np.ndarray, scale: float) -> dict[int, tuple[float, float]]:
    """id -> centro (en px de la imagen original) de cada marcador detectado
    en `image`, que está reducida por `scale`."""
    detector = cv2.aruco.ArucoDetector(ARUCO_DICT, _ARUCO_PARAMS)
    corners, ids, _ = detector.detectMarkers(image)
    found: dict[int, tuple[float, float]] = {}
    if ids is None:
        logger.debug("escala %.2f: ningún marcador", scale)
        return found
    for marker_corners, marker_id in zip(corners, ids.flatten()):
        center = marker_corners.reshape(4, 2).mean(axis=0) / scale
        found[int(marker_id)] = (float(center[0]), float(center[1]))
        logger.debug("escala %.2f: marcador id=%d con centro en (%.1f, %.1f) px",
                     scale, int(marker_id), *center)
    return found


def align_sheet(
    image: np.ndarray,
    markers: DetectedMarkers | None = None,
    size: tuple[int, int] = CANONICAL_SIZE,
) -> np.ndarray:
    """Corrige perspectiva y devuelve la hoja "aplanada" a `size` (ancho, alto).

    El centro de cada marcador queda en la esquina correspondiente de la
    imagen resultante. Si no se pasan `markers` ya detectados, los detecta
    internamente.
    """
    logger.info("inicio: alineando hoja a %dx%d px", *size)
    if markers is None:
        logger.debug("no se pasaron marcadores, se detectan aquí")
        markers = detect_corner_markers(image)

    width, height = size

    src_points = np.array(
        [
            markers.centers["top_left"],
            markers.centers["top_right"],
            markers.centers["bottom_right"],
            markers.centers["bottom_left"],
        ],
        dtype=np.float32,
    )
    dst_points = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )

    transform = cv2.getPerspectiveTransform(src_points, dst_points)
    logger.debug("matriz de perspectiva:\n%s", transform)
    aligned = cv2.warpPerspective(image, transform, (width, height))
    logger.info("fin: hoja alineada")
    return aligned


def warp_region(
    image: np.ndarray,
    markers: DetectedMarkers,
    page_marker_centers: dict[str, tuple[float, float]],
    region: tuple[float, float, float, float],
    px_per_mm: float,
) -> np.ndarray:
    """Endereza solo una región de la hoja, a la resolución que se pida.

    `page_marker_centers` y `region` (x, y, ancho, alto) están en mm de
    página. Sirve para leer detalles finos (como el QR) a más resolución
    que la imagen alineada completa, sin pagar el costo de enderezar toda
    la hoja a esa resolución.
    """
    logger.debug("enderezando región %s mm a %.1f px/mm", region, px_per_mm)
    order = list(CORNER_IDS)
    src = np.array([markers.centers[k] for k in order], dtype=np.float32)
    x, y, w, h = region
    dst = np.array(
        [page_marker_centers[k] for k in order], dtype=np.float32
    )
    dst = (dst - np.float32([x, y])) * np.float32(px_per_mm)

    transform = cv2.getPerspectiveTransform(src, dst)
    size = (round(w * px_per_mm), round(h * px_per_mm))
    logger.debug("región enderezada de %dx%d px", *size)
    return cv2.warpPerspective(image, transform, size)
