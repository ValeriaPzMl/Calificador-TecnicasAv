"""
Dada una foto de la hoja, detectar los 4 marcadores de esquina y guardar
la hoja ya "aplanada" por perspectiva.

Uso:
    python -m cli.align data/raw/foto1.jpg
    python -m cli.align data/raw/foto1.jpg --exam data/examples/ejemplo.json
    python -m cli.align data/raw/foto1.jpg --exam data/examples/ejemplo.json -v

Guarda el resultado en data/processed/foto1_aligned.png.

Con --exam (para hojas generadas con `cli.make_sheet`), además:
- usa el tamaño canónico del layout de ese examen,
- lee el QR y avisa si la foto es de otro examen,
- guarda data/processed/foto1_overlay.png con un círculo verde donde el
  sistema espera cada burbuja. Si los círculos caen sobre las burbujas
  impresas, la alineación funciona y la fase 2 (detección) puede leerlas.
"""

import argparse
import logging
from pathlib import Path

import cv2

from cli.logs import add_verbose_flag, setup_logging
from core.alignment import AlignmentError, align_sheet, detect_corner_markers
from core.layout import SheetLayout, build_layout
from core.preprocessing import load_image
from core.qr import read_exam_id
from models.exam import load_exam

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

logger = logging.getLogger(__name__)


def draw_layout_overlay(aligned, layout: SheetLayout):
    """Dibuja en verde dónde espera el layout cada burbuja."""
    logger.info("dibujando overlay de burbujas esperadas")
    overlay = aligned.copy()
    for bubble in layout.all_bubbles():
        cx, cy, r = layout.bubble_px(bubble)
        cv2.circle(overlay, (round(cx), round(cy)), round(r), (0, 200, 0), 2)
    return overlay


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("image_path", help="Ruta a la foto de la hoja de respuestas")
    parser.add_argument("--exam", help="JSON del examen con el que se generó la hoja")
    parser.add_argument("--page", default="letter", help="Tamaño de página de la hoja")
    add_verbose_flag(parser)
    args = parser.parse_args()
    setup_logging(args.verbose)
    logger.info("inicio: image_path=%s exam=%s page=%s", args.image_path, args.exam, args.page)

    image = load_image(args.image_path)
    layout = build_layout(load_exam(args.exam), page=args.page) if args.exam else None

    try:
        markers = detect_corner_markers(image)
        if layout is None:
            aligned = align_sheet(image, markers)
        else:
            aligned = align_sheet(image, markers, size=layout.canonical_size)
    except AlignmentError as exc:
        logger.error("falló la alineación")
        print(f"ERROR: {exc}")
        raise SystemExit(1)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(args.image_path).stem
    out_path = PROCESSED_DIR / f"{stem}_aligned.png"
    cv2.imwrite(str(out_path), aligned)
    print(f"OK. Hoja alineada guardada en: {out_path}")

    if layout is not None:
        exam_id = read_exam_id(image, markers)
        if exam_id is None:
            print("AVISO: no se pudo leer el QR de la hoja.")
        elif exam_id != layout.exam_id:
            print(f"AVISO: el QR dice que esta hoja es del examen '{exam_id}', "
                  f"no de '{layout.exam_id}'.")
        else:
            print(f"    QR leído: examen '{exam_id}'")

        overlay_path = PROCESSED_DIR / f"{stem}_overlay.png"
        cv2.imwrite(str(overlay_path), draw_layout_overlay(aligned, layout))
        print(f"    Revisa que los círculos verdes caigan sobre las burbujas: {overlay_path}")

    logger.info("fin")


if __name__ == "__main__":
    main()
