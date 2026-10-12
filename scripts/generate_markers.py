"""
Genera las 4 imágenes de marcadores ArUco que debes pegar/imprimir en las
esquinas de tu plantilla de hoja de respuestas.

Uso:
    python scripts/generate_markers.py

Genera templates/marker_0_top_left.png, marker_1_top_right.png, etc.
Imprímelos y pégalos (o diséñalos directo en el documento con Word/Canva/
LaTeX) en las 4 esquinas de tu hoja, respetando la posición de cada ID:

    0 --------------- 1
    |                 |
    |     preguntas    |
    |                 |
    3 --------------- 2

Tamaño recomendado de impresión: 2x2 cm es suficiente para que la cámara
del celular los detecte bien a distancia normal de escritorio.
"""

import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.alignment import ARUCO_DICT, CORNER_IDS  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "templates"
MARKER_PIXELS = 400  # resolución de la imagen generada, no el tamaño impreso


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    for corner_name, marker_id in CORNER_IDS.items():
        marker_image = cv2.aruco.generateImageMarker(
            ARUCO_DICT, marker_id, MARKER_PIXELS
        )
        out_path = OUTPUT_DIR / f"marker_{marker_id}_{corner_name}.png"
        cv2.imwrite(str(out_path), marker_image)
        print(f"Generado: {out_path}")


if __name__ == "__main__":
    main()
