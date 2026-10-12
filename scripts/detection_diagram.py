"""
Diagrama de alto nivel: cómo se encuentra cada pregunta en la foto y cómo
se decide qué respuesta marcó el alumno. Incluye dos ejemplos reales.

Uso:
    python scripts/detection_diagram.py

Guarda docs/deteccion.pdf y docs/deteccion.png. Usa el mismo dibujador que
scripts/pipeline_diagram.py (el detalle función por función está ahí).
"""

from reportlab.lib.colors import HexColor
from reportlab.pdfgen.canvas import Canvas

import pypdfium2 as pdfium
from pipeline_diagram import (
    COL_GAP, CW, INK, MARGIN, MUTED, OK, OUT_DIR, PNG_SCALE, Chart, D, E, L, N, P, S,
    draw_shapes_legend, register_fonts,
)

WHERE = [
    S("Foto de la hoja contestada (celular, cualquier ángulo)"),
    P("Buscar los 4 marcadores de las esquinas: cada uno tiene un patrón distinto (ids 0-3), "
      "así se sabe cuál es cada esquina aunque la hoja esté girada — detect_corner_markers()"),
    P("Si falta alguno, reintentar con la foto más chica: un marcador rayado por la impresora "
      "se vuelve a ver sólido"),
    D("¿se ven los 4 marcadores?", "no", "Error: revisar la foto o la impresión", "err", "sí"),
    P("Leer el QR, que está en el mismo lugar en todas las hojas: dice de qué examen es "
      "(p. ej. 'EG1:ingles01') — read_qr()"),
    P("Traer ese examen de la BD: cuántas opciones, qué preguntas tiene cada sección, "
      "cuántos dígitos de matrícula — get_exam()"),
    P("Recalcular el layout con la MISMA cuenta que dibujó el PDF: da la posición en mm de "
      "cada burbuja — build_layout()"),
    P("Enderezar la foto: cada marcador va a una esquina de una imagen fija "
      "(900 × 1217 px, 5 px por mm) — align_sheet()"),
    P("Convertir mm → px: 'pregunta 5, opción B' = un centro (x, y) y un radio en la hoja "
      "enderezada — to_px()"),
    E("Se sabe exactamente dónde está cada burbuja de cada pregunta y de la matrícula. "
      "No se busca nada: se calcula."),
]

WHAT = [
    S("Hoja enderezada + posición de cada burbuja"),
    L("Para cada burbuja"),
    P("papel = qué tan claro es el papel alrededor (así una sombra no confunde)"),
    P("centro = gris promedio del centro de la burbuja, sin el contorno impreso"),
    P("fill = 1 − centro ÷ papel → 0 es papel, 1 es negro (vacía ≈ 0.1; lápiz en foto ≈ 0.4–0.6)"
      " — bubble_fill()"),
    N("siguiente burbuja"),
    P("Calibrar con esta hoja: 'vacía' = cómo se ve una burbuja sin marcar; 'marca típica' = "
      "cómo marca este alumno — calibrate()"),
    P("Normalizar cada fill: 0 = como vacía, 1 = como la marca típica (así da igual lápiz "
      "suave, fuerte o pluma)"),
    L("Para cada pregunta"),
    P("rellenas = burbujas ≥ 0.55; dudosas = entre 0.25 y 0.55 — classify()"),
    D("¿exactamente 1 rellena y le gana a la segunda por ≥ 0.35?", "sí",
      "CONTESTADA: se guarda esa letra", "ok", "no"),
    D("¿ninguna rellena ni dudosa?", "sí", "EN BLANCO: vale 0", "ok", "no"),
    P("AMBIGUA (dos marcas, marca tenue, a medias, tachada…): vale 0 y se marca para "
      "revisión. Nunca se adivina."),
    N("siguiente pregunta"),
    P("La matrícula se lee igual: cada dígito es una fila de 0 a 9; si uno no se lee "
      "queda '?' (p. ej. 20?517)"),
    E("Matrícula + respuesta de cada pregunta → se califica con score()"),
]

EXAMPLE_ANSWERED = [
    S("Ejemplo real: foto 04 (historia01), pregunta 47"),
    P("fills medidos: A 0.10 · B 0.12 · C 0.08 · D 0.27 · E 0.10"),
    P("calibración de esa hoja: vacía 0.09, marca típica 0.39 (el alumno marca suave)"),
    P("normalizados: A 0.04 · B 0.12 · C −0.02 · D 0.60 · E 0.05"),
    P("rellenas: solo D (0.60 ≥ 0.55) · dudosas: ninguna"),
    D("¿1 rellena y margen ≥ 0.35?", "sí",
      "D le gana a B por 0.48 → CONTESTADA: D", "ok", "no"),
    E("(si no, se revisaría si está en blanco o es ambigua)"),
]

EXAMPLE_AMBIGUOUS = [
    S("Ejemplo real: foto 06 (ejemplo0001), pregunta 13"),
    P("fills medidos: A 0.36 · B 0.38 · C 0.09 · D 0.11"),
    P("calibración de esa hoja: vacía 0.09, marca típica 0.42"),
    P("normalizados: A 0.82 · B 0.87 · C 0.00 · D 0.05"),
    P("rellenas: A y B (las dos ≥ 0.55)"),
    D("¿1 rellena y margen ≥ 0.35?", "sí", "(no aplica)", "ok", "no: hay 2"),
    D("¿ninguna rellena ni dudosa?", "sí", "(no aplica)", "ok", "no"),
    E("AMBIGUA: el alumno marcó A y B. Vale 0 y el profesor la revisa (naranja en la "
      "imagen de revisión)"),
]

PANELS = [
    ("1", "¿Dónde está cada pregunta?", "core/alignment.py · core/qr.py · core/layout.py",
     WHERE, ("#1F64B0", "#D9E8F8")),
    ("2", "¿Qué respuesta marcó?", "core/detection.py", WHAT, ("#B03535", "#F8DCDC")),
    ("2a", "Ejemplo: se acepta", "datos reales de tus fotos", EXAMPLE_ANSWERED,
     ("#2F8040", "#DAF0DD")),
    ("2b", "Ejemplo: queda ambigua", "datos reales de tus fotos", EXAMPLE_AMBIGUOUS,
     ("#C77700", "#FBEBD2")),
]


def main() -> None:
    register_fonts()
    charts = [Chart(f"panel{badge}", title, file, steps, badge=badge, colors=colors)
              for badge, title, file, steps, colors in PANELS]

    legend_h = 100
    page_w = 2 * MARGIN + len(charts) * CW + (len(charts) - 1) * COL_GAP
    page_h = MARGIN + legend_h + max(ch.height for ch in charts) + MARGIN

    OUT_DIR.mkdir(exist_ok=True)
    pdf_path = OUT_DIR / "deteccion.pdf"
    c = Canvas(str(pdf_path), pagesize=(page_w, page_h))
    c.setTitle("Cómo se detectan preguntas y respuestas")
    c.setFillColor(HexColor("#FAFAF7"))
    c.rect(0, 0, page_w, page_h, stroke=0, fill=1)

    top = page_h - MARGIN
    c.setFillColor(INK)
    c.setFont("Sans-Bold", 22)
    c.drawString(MARGIN, top - 22, "Cómo se detectan las preguntas y las respuestas")
    c.setFont("Sans", 10)
    c.setFillColor(MUTED)
    c.drawString(MARGIN, top - 40, "Vista de alto nivel. El detalle función por función está "
                 "en docs/pipeline_calificacion.pdf. Generado por scripts/detection_diagram.py")
    draw_shapes_legend(c, MARGIN, top - 66, extra=[("Resultado", OK, 4)])

    for i, chart in enumerate(charts):
        chart.draw(c, MARGIN + i * (CW + COL_GAP), top - legend_h)
    c.showPage()
    c.save()

    png_path = pdf_path.with_suffix(".png")
    pdfium.PdfDocument(str(pdf_path))[0].render(scale=PNG_SCALE).to_pil().save(png_path)
    print(f"OK. PDF: {pdf_path}")
    print(f"    PNG: {png_path}")


if __name__ == "__main__":
    main()
