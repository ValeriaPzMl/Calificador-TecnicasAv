"""
Genera el PDF imprimible de la hoja de respuestas de un examen.

Uso:
    python -m cli.make_sheet data/examples/ejemplo.json
    python -m cli.make_sheet data/examples/ejemplo.json --preview
    python -m cli.make_sheet examen.json -o hoja.pdf --page a4
    python -m cli.make_sheet data/examples/ejemplo.json -v     # ver el flujo
    python -m cli.make_sheet data/examples/ejemplo.json -vv    # flujo + detalle

Por defecto guarda en data/sheets/<id del examen>.pdf. Con --preview
también guarda un PNG de la hoja para revisarla sin abrir el PDF.
"""

import argparse
import logging
from pathlib import Path

from cli.logs import add_verbose_flag, setup_logging
from core.layout import PAGE_SIZES_MM, LayoutError, build_layout
from core.sheet_pdf import render
from models.exam import Exam, ExamValidationError, load_exam

SHEETS_DIR = Path(__file__).resolve().parent.parent / "data" / "sheets"
PREVIEW_DPI = 150

logger = logging.getLogger(__name__)


def write_sheet(exam: Exam, page: str = "letter", output: str | None = None,
                preview: bool = False) -> Path:
    """Calcula el layout, dibuja el PDF y lo guarda. Lo usan este comando
    (examen desde JSON) y `cli.exams sheet` (examen desde la BD)."""
    logger.info("inicio: hoja del examen '%s', página %s", exam.id, page)
    layout = build_layout(exam, page=page)

    out_path = Path(output) if output else SHEETS_DIR / f"{exam.id}.pdf"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("dibujando el PDF y guardándolo en %s", out_path)
    out_path.write_bytes(render(layout))
    print(f"OK. Hoja generada: {out_path}")
    print(
        f"    {exam.total_questions} preguntas en {len(exam.sections)} secciones, "
        f"matrícula de {exam.student_id_digits} dígitos"
    )

    if preview:
        import pypdfium2 as pdfium

        logger.info("generando vista previa PNG a %d dpi", PREVIEW_DPI)
        pdf = pdfium.PdfDocument(str(out_path))
        image = pdf[0].render(scale=PREVIEW_DPI / 72).to_pil()
        preview_path = out_path.with_suffix(".png")
        image.save(preview_path)
        logger.info("vista previa guardada: %s (%dx%d px)", preview_path, *image.size)
        print(f"    Vista previa: {preview_path}")

    logger.info("fin: %s", out_path)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("exam_path", help="Ruta al JSON del examen")
    parser.add_argument("-o", "--output", help="Ruta del PDF a generar")
    parser.add_argument("--page", default="letter", choices=list(PAGE_SIZES_MM))
    parser.add_argument("--preview", action="store_true", help="Guardar también un PNG")
    add_verbose_flag(parser)
    args = parser.parse_args()
    setup_logging(args.verbose)
    logger.info("inicio: exam_path=%s output=%s page=%s preview=%s",
                args.exam_path, args.output, args.page, args.preview)

    try:
        logger.info("paso 1/2: cargar y validar el examen")
        exam = load_exam(args.exam_path)
        logger.info("paso 2/2: generar la hoja")
        write_sheet(exam, args.page, args.output, args.preview)
    except (ExamValidationError, LayoutError) as exc:
        logger.error("falló la preparación: %s", type(exc).__name__)
        print(f"ERROR: {exc}")
        raise SystemExit(1)
    logger.info("fin")


if __name__ == "__main__":
    main()
