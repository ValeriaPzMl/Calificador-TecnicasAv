"""
Dibuja un SheetLayout como PDF imprimible.

Este módulo no decide nada de posiciones: todo sale de `layout.py`. Si
algo se ve mal colocado, el arreglo va en el layout, no aquí. Así el PDF
y el detector nunca se desincronizan.

Los marcadores ArUco y el QR se dibujan como vectores (no como imágenes
pegadas), para que salgan nítidos a cualquier resolución de impresión.
"""

import io
import logging

import cv2
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from .alignment import ARUCO_DICT, CORNER_IDS
from .layout import Bubble, SheetLayout, Text

MIN_FONT_SIZE = 5.0
BUBBLE_LINE_WIDTH = 0.6  # pt; delgado para que una burbuja vacía no parezca rellena
BUBBLE_LETTER_GRAY = 0.55

logger = logging.getLogger(__name__)


def render(layout: SheetLayout) -> bytes:
    """Devuelve los bytes del PDF de la hoja de respuestas."""
    logger.info("inicio: PDF de la hoja '%s'", layout.exam_id)
    buffer = io.BytesIO()
    page_w, page_h = layout.page_size
    logger.debug("canvas de %.1fx%.1f mm", page_w, page_h)
    canvas = Canvas(buffer, pagesize=(page_w * mm, page_h * mm))
    canvas.setTitle(f"Hoja de respuestas {layout.exam_id}")
    canvas.setCreator("exam-grader")

    # Reportlab tiene el origen abajo a la izquierda; el layout, arriba.
    def y(mm_from_top: float) -> float:
        return (page_h - mm_from_top) * mm

    logger.info("paso 1/5: %d marcadores ArUco", len(CORNER_IDS))
    for corner_name, marker_id in CORNER_IDS.items():
        cx, cy = layout.marker_centers[corner_name]
        _draw_marker(canvas, marker_id, cx, cy, layout.marker_size, y)

    logger.info("paso 2/5: QR")
    _draw_qr(canvas, layout, y)

    logger.info("paso 3/5: %d cuadros y %d líneas", len(layout.boxes), len(layout.lines))
    canvas.setStrokeGray(0)
    canvas.setLineWidth(0.5)
    for box in layout.boxes:
        canvas.rect(box.x * mm, y(box.y + box.h), box.w * mm, box.h * mm)
    for line in layout.lines:
        canvas.line(line.x1 * mm, y(line.y1), line.x2 * mm, y(line.y2))

    logger.info("paso 4/5: %d textos", len(layout.texts))
    for text in layout.texts:
        _draw_text(canvas, text, y)

    bubbles = layout.all_bubbles()
    logger.info("paso 5/5: %d burbujas", len(bubbles))
    for bubble in bubbles:
        _draw_bubble(canvas, bubble, y)

    canvas.showPage()
    canvas.save()
    pdf = buffer.getvalue()
    logger.info("fin: PDF de %d bytes", len(pdf))
    return pdf


def _draw_marker(canvas, marker_id, cx, cy, size, y) -> None:
    """Dibuja el marcador celda por celda a partir de su matriz de bits."""
    cells = ARUCO_DICT.markerSize + 2  # + borde negro de 1 celda por lado
    logger.debug("marcador id=%d en (%.1f, %.1f) mm, lado %.1f mm, %dx%d celdas",
                 marker_id, cx, cy, size, cells, cells)
    bits = cv2.aruco.generateImageMarker(ARUCO_DICT, marker_id, cells)
    cell = size / cells
    x0 = cx - size / 2
    y0 = cy - size / 2
    canvas.setFillGray(0)
    for row in range(cells):
        for col in range(cells):
            if bits[row, col] == 0:  # 0 = celda negra
                canvas.rect(
                    (x0 + col * cell) * mm,
                    y(y0 + (row + 1) * cell),
                    cell * mm,
                    cell * mm,
                    stroke=0,
                    fill=1,
                )


def _draw_qr(canvas, layout: SheetLayout, y) -> None:
    box = layout.qr_box
    logger.debug("QR '%s' en (%.1f, %.1f) mm, %.1fx%.1f mm",
                 layout.qr_payload, box.x, box.y, box.w, box.h)
    widget = QrCodeWidget(layout.qr_payload, barLevel="M")
    x1, y1, x2, y2 = widget.getBounds()
    size = box.w * mm
    drawing = Drawing(
        size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0]
    )
    drawing.add(widget)
    renderPDF.draw(drawing, canvas, box.x * mm, y(box.y + box.h))


def _draw_text(canvas, text: Text, y) -> None:
    font = "Helvetica-Bold" if text.bold else "Helvetica"
    size = text.size
    if text.max_width is not None:
        while size > MIN_FONT_SIZE and stringWidth(text.text, font, size) > text.max_width * mm:
            size -= 0.25
        if size < text.size:
            logger.debug("'%s' no cabía en %.1f mm: letra reducida de %g a %g pt",
                         text.text, text.max_width, text.size, size)
    logger.debug("texto '%s' en (%.1f, %.1f) mm, %s %g pt, alineado a %s",
                 text.text, text.x, text.y, font, size, text.align)
    canvas.setFont(font, size)
    canvas.setFillGray(text.gray)
    draw = {
        "left": canvas.drawString,
        "center": canvas.drawCentredString,
        "right": canvas.drawRightString,
    }[text.align]
    draw(text.x * mm, y(text.y), text.text)


def _draw_bubble(canvas, bubble: Bubble, y) -> None:
    logger.debug("burbuja '%s' en (%.1f, %.1f) mm, radio %.1f mm",
                 bubble.value, bubble.cx, bubble.cy, bubble.r)
    canvas.setStrokeGray(0)
    canvas.setLineWidth(BUBBLE_LINE_WIDTH)
    canvas.circle(bubble.cx * mm, y(bubble.cy), bubble.r * mm, stroke=1, fill=0)

    font_size = 0.6 * (2 * bubble.r) * mm  # 60% del diámetro, en pt
    canvas.setFont("Helvetica", font_size)
    canvas.setFillGray(BUBBLE_LETTER_GRAY)
    canvas.drawCentredString(bubble.cx * mm, y(bubble.cy) - font_size * 0.35, bubble.value)
