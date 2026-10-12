"""
Carga y preparación básica de la imagen antes de cualquier detección.

Todo lo que entra aquí es una foto "cruda" tomada con celular: puede venir
con mala iluminación, algo de ruido o ángulo. Estas funciones no saben nada
de burbujas, exámenes ni calificaciones — solo dejan la imagen lista para
que `alignment.py` pueda encontrar los marcadores.
"""

import logging
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def load_image(path: str | Path) -> np.ndarray:
    """Carga una imagen desde disco en formato BGR (el nativo de OpenCV).

    Lanza FileNotFoundError si la ruta no existe o no es una imagen válida,
    en vez de dejar que el resto del pipeline reciba None y falle más
    adelante con un error confuso.
    """
    logger.info("inicio: cargando %s", path)
    path = Path(path)
    if not path.exists():
        logger.warning("no existe: %s", path)
        raise FileNotFoundError(f"No existe la imagen: {path}")

    image = cv2.imread(str(path))
    if image is None:
        logger.warning("OpenCV no pudo leer: %s", path)
        raise FileNotFoundError(f"OpenCV no pudo leer la imagen: {path}")

    logger.info("fin: imagen de %dx%d px", image.shape[1], image.shape[0])
    return image


def to_grayscale(image: np.ndarray) -> np.ndarray:
    """Convierte a escala de grises. Idempotente: si ya viene en gris, la
    regresa tal cual en vez de fallar."""
    if len(image.shape) == 2:
        logger.debug("ya estaba en gris")
        return image
    logger.debug("BGR -> gris (%dx%d px)", image.shape[1], image.shape[0])
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def denoise(gray: np.ndarray) -> np.ndarray:
    """Blur suave para quitar ruido de sensor sin borrar bordes útiles."""
    logger.debug("blur gaussiano 5x5")
    return cv2.GaussianBlur(gray, (5, 5), 0)


def adaptive_threshold(gray: np.ndarray) -> np.ndarray:
    """Binariza con umbral adaptativo: funciona mucho mejor que un umbral
    fijo cuando la foto tiene iluminación despareja (una esquina más oscura
    que otra), que es el caso normal de una foto tomada con celular.
    """
    logger.debug("umbral adaptativo (blockSize=35, C=11)")
    return cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        blockSize=35,
        C=11,
    )


def prepare(image: np.ndarray) -> np.ndarray:
    """Pipeline corto: gris -> blur -> threshold adaptativo.

    Devuelve una imagen binaria (blanco = trazo/marca, negro = fondo),
    lista para buscar contornos o marcadores.
    """
    logger.info("gris -> blur -> umbral adaptativo")
    gray = to_grayscale(image)
    gray = denoise(gray)
    return adaptive_threshold(gray)
