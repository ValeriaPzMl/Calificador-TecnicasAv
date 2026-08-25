"""
Primer hito comprobable del proyecto: dada una foto de la hoja, detectar
los 4 marcadores de esquina y guardar la hoja ya "aplanada" por perspectiva.

Uso:
    python -m cli.align data/raw/foto1.jpg

Guarda el resultado en data/processed/foto1_aligned.png. Si esto se ve
recto y del tamaño correcto (1000x1400), el siguiente paso es diseñar la
cuadrícula de burbujas sobre esa imagen aplanada.
"""

import argparse
from pathlib import Path

import cv2

from core.alignment import AlignmentError, align_sheet
from core.preprocessing import load_image

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image_path", help="Ruta a la foto de la hoja de respuestas")
    args = parser.parse_args()

    image = load_image(args.image_path)

    try:
        aligned = align_sheet(image)
    except AlignmentError as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(args.image_path).stem
    out_path = PROCESSED_DIR / f"{stem}_aligned.png"
    cv2.imwrite(str(out_path), aligned)

    print(f"OK. Hoja alineada guardada en: {out_path}")


if __name__ == "__main__":
    main()
