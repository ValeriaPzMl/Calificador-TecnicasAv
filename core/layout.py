"""
Layout de la hoja de respuestas: dónde va cada cosa, calculado a partir de
un Exam.

Esta es la pieza que une el PDF con el detector. `sheet_pdf.py` dibuja
exactamente lo que dice el layout, y `detection.py` (fase 2) busca las
burbujas exactamente donde dice el layout. Como ambos leen la misma
fuente, no hay que calibrar coordenadas a mano.

Unidades:
- Todo se define en **milímetros** sobre la página, con origen en la
  esquina superior izquierda y el eje y hacia abajo (como en una imagen).
- `to_px` convierte a **píxeles canónicos**: las coordenadas de la imagen
  que devuelve `alignment.align_sheet`. Ahí el origen es el centro del
  marcador superior izquierdo, porque la alineación mapea los centros de
  los marcadores a las esquinas de la imagen.

Por eso todo lo que se va a leer (burbujas, QR) se coloca dentro del
rectángulo formado por los centros de los 4 marcadores.
"""

import logging
from dataclasses import dataclass, field

from models.exam import Exam

from .alignment import CORNER_IDS

PAGE_SIZES_MM = {
    "letter": (215.9, 279.4),
    "a4": (210.0, 297.0),
}

PAGE_MARGIN = 10.0  # distancia del borde de la hoja al marcador
MARKER_SIZE = 16.0  # lado del marcador ArUco impreso, incluyendo su borde negro
PX_PER_MM = 5.0  # resolución de la imagen alineada (~127 dpi)

QR_PREFIX = "EG1:"  # el QR contiene QR_PREFIX + exam.id
QR_SIZE = 24.0

# Matrícula: una fila por dígito, 10 burbujas (0-9) por fila
SID_BUBBLE_R = 2.1
SID_PITCH = 5.5
SID_BOX = 5.0  # cuadro para escribir el dígito a mano

# Preguntas
Q_BUBBLE_R = 2.3
Q_PITCH_X = 6.5
Q_ROW_H = 6.5
Q_LABEL_W = 8.0  # espacio para el número de pregunta
Q_COL_GAP = 5.0

logger = logging.getLogger(__name__)


class LayoutError(ValueError):
    """El examen no cabe en una hoja con este diseño."""


@dataclass(frozen=True)
class Bubble:
    cx: float  # mm
    cy: float  # mm
    r: float  # mm
    value: str  # letra de opción ("A") o dígito de matrícula ("7")


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class Text:
    x: float
    y: float  # línea base
    text: str
    size: float = 8.0
    bold: bool = False
    align: str = "left"  # "left" | "center" | "right"
    max_width: float | None = None  # el PDF reduce la letra si no cabe
    gray: float = 0.0  # 0 = negro, 1 = blanco


@dataclass(frozen=True)
class Line:
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass
class SheetLayout:
    exam_id: str
    page_size: tuple[float, float]  # (ancho, alto) en mm
    marker_size: float
    marker_centers: dict[str, tuple[float, float]]  # nombre de esquina -> (x, y) mm
    qr_box: Box
    qr_payload: str
    student_id: list[list[Bubble]]  # [posición de dígito][0..9]
    questions: dict[int, list[Bubble]]  # número de pregunta -> burbujas A, B, ...
    texts: list[Text] = field(default_factory=list)
    boxes: list[Box] = field(default_factory=list)  # cuadros de escritura a mano
    lines: list[Line] = field(default_factory=list)
    px_per_mm: float = PX_PER_MM

    # --- Conversión a la imagen alineada ---

    @property
    def _aligned_mm(self) -> tuple[float, float]:
        """Ancho y alto (mm) del rectángulo entre centros de marcadores."""
        tl = self.marker_centers["top_left"]
        br = self.marker_centers["bottom_right"]
        return br[0] - tl[0], br[1] - tl[1]

    @property
    def canonical_size(self) -> tuple[int, int]:
        """(ancho, alto) en px de la imagen alineada. Se le pasa a
        `alignment.align_sheet`."""
        w_mm, h_mm = self._aligned_mm
        return round(w_mm * self.px_per_mm), round(h_mm * self.px_per_mm)

    def to_px(self, x: float, y: float) -> tuple[float, float]:
        """mm de página -> px en la imagen alineada.

        `align_sheet` manda el centro del marcador top_right a (ancho - 1, 0),
        así que la escala es (ancho_px - 1) / ancho_mm y no px_per_mm exacto.
        """
        tl = self.marker_centers["top_left"]
        w_mm, h_mm = self._aligned_mm
        w_px, h_px = self.canonical_size
        px = (
            (x - tl[0]) * (w_px - 1) / w_mm,
            (y - tl[1]) * (h_px - 1) / h_mm,
        )
        logger.debug("(%.2f, %.2f) mm -> (%.1f, %.1f) px", x, y, *px)
        return px

    def bubble_px(self, bubble: Bubble) -> tuple[float, float, float]:
        """(cx, cy, r) de una burbuja en px de la imagen alineada."""
        cx, cy = self.to_px(bubble.cx, bubble.cy)
        r = bubble.r * self.px_per_mm
        logger.debug("burbuja '%s' -> centro (%.1f, %.1f) px, radio %.1f px", bubble.value, cx, cy, r)
        return cx, cy, r

    def all_bubbles(self) -> list[Bubble]:
        bubbles = [b for row in self.student_id for b in row]
        for options in self.questions.values():
            bubbles.extend(options)
        logger.debug("%d burbujas (%d de matrícula + %d de preguntas)",
                     len(bubbles), sum(len(r) for r in self.student_id),
                     sum(len(o) for o in self.questions.values()))
        return bubbles


@dataclass(frozen=True)
class PageGeometry:
    """La parte del layout que NO depende del examen: marcadores, QR y
    zona de contenido. Permite leer el QR de una foto antes de saber qué
    examen es (y por lo tanto antes de poder construir su layout)."""

    size: tuple[float, float]
    marker_centers: dict[str, tuple[float, float]]
    qr_box: Box
    left: float
    right: float
    top: float
    bottom: float


def page_geometry(page: str = "letter") -> PageGeometry:
    logger.info("inicio: geometría de página '%s'", page)
    if page not in PAGE_SIZES_MM:
        logger.warning("tamaño de página desconocido: %s", page)
        raise ValueError(f"Tamaño de página desconocido: {page}. Usa {list(PAGE_SIZES_MM)}")

    page_w, page_h = PAGE_SIZES_MM[page]
    near = PAGE_MARGIN + MARKER_SIZE / 2
    marker_centers = {
        "top_left": (near, near),
        "top_right": (page_w - near, near),
        "bottom_right": (page_w - near, page_h - near),
        "bottom_left": (near, page_h - near),
    }
    assert set(marker_centers) == set(CORNER_IDS)

    # Zona de contenido legible: dentro del rectángulo de centros de
    # marcadores y por debajo/encima de los marcadores mismos.
    left = near
    right = page_w - near
    top = PAGE_MARGIN + MARKER_SIZE + 4
    bottom = page_h - PAGE_MARGIN - MARKER_SIZE - 4

    geo = PageGeometry(
        size=(page_w, page_h),
        marker_centers=marker_centers,
        qr_box=Box(right - QR_SIZE, top, QR_SIZE, QR_SIZE),
        left=left,
        right=right,
        top=top,
        bottom=bottom,
    )
    logger.debug("centros de marcadores (mm): %s", marker_centers)
    logger.debug("QR en %s", geo.qr_box)
    logger.info("fin: página %.1fx%.1f mm, zona de contenido x=[%.1f, %.1f] y=[%.1f, %.1f]",
                page_w, page_h, left, right, top, bottom)
    return geo


def build_layout(exam: Exam, page: str = "letter") -> SheetLayout:
    """Calcula el layout completo de la hoja para `exam`.

    Lanza LayoutError si las preguntas no caben en una sola hoja.
    """
    logger.info("inicio: layout del examen '%s' en página '%s'", exam.id, page)
    exam.validate()
    geo = page_geometry(page)
    page_w, _ = geo.size
    left, right, top, bottom = geo.left, geo.right, geo.top, geo.bottom

    texts: list[Text] = []
    boxes: list[Box] = []
    lines: list[Line] = []

    # --- Encabezado: entre los dos marcadores de arriba ---
    logger.info("bloque 1/4: encabezado")
    header_w = right - left - MARKER_SIZE
    texts.append(
        Text(page_w / 2, PAGE_MARGIN + 6, exam.name, size=14, bold=True,
             align="center", max_width=header_w)
    )
    total_points = sum(s.max_points for s in exam.sections)
    texts.append(
        Text(page_w / 2, PAGE_MARGIN + 12,
             f"{exam.total_questions} preguntas · {total_points:g} puntos · ID {exam.id}",
             size=8, align="center", max_width=header_w, gray=0.35)
    )

    logger.debug("encabezado: '%s', %d preguntas, %g puntos en total",
                 exam.name, exam.total_questions, total_points)

    # --- Bloque superior izquierdo: matrícula ---
    logger.info("bloque 2/4: matrícula de %d dígitos", exam.student_id_digits)
    texts.append(Text(left, top + 3, "Matrícula", size=9, bold=True))
    student_id: list[list[Bubble]] = []
    first_bubble_x = left + SID_BOX + 3 + SID_BUBBLE_R
    for pos in range(exam.student_id_digits):
        cy = top + 7 + pos * SID_PITCH + SID_PITCH / 2
        boxes.append(Box(left, cy - SID_BOX / 2, SID_BOX, SID_BOX))
        student_id.append([
            Bubble(first_bubble_x + d * SID_PITCH, cy, SID_BUBBLE_R, str(d))
            for d in range(10)
        ])
        logger.debug("dígito %d de matrícula: fila en y=%.1f mm", pos + 1, cy)
    sid_right = first_bubble_x + 9 * SID_PITCH + SID_BUBBLE_R
    sid_bottom = top + 7 + exam.student_id_digits * SID_PITCH

    # --- Bloque superior derecho: QR (posición fija por página) ---
    qr_box = geo.qr_box
    logger.info("bloque 3/4: QR con '%s' y datos del alumno", QR_PREFIX + exam.id)

    # --- Bloque central: datos del alumno e instrucciones ---
    info_x = sid_right + 8
    info_right = qr_box.x - 6
    info_w = info_right - info_x
    texts.append(Text(info_x, top + 5, "Nombre:", size=9, bold=True))
    lines.append(Line(info_x + 15, top + 5.5, info_right, top + 5.5))
    texts.append(Text(info_x, top + 13, "Grupo:", size=9, bold=True))
    lines.append(Line(info_x + 13, top + 13.5, info_x + info_w / 2 - 3, top + 13.5))
    texts.append(Text(info_x + info_w / 2, top + 13, "Fecha:", size=9, bold=True))
    lines.append(Line(info_x + info_w / 2 + 13, top + 13.5, info_right, top + 13.5))
    instructions = [
        "Instrucciones:",
        "• Rellena por completo un solo círculo por pregunta.",
        "• Escribe tu matrícula en los cuadros y rellena",
        "   el círculo de cada dígito.",
        "• No dobles ni manches la hoja, sobre todo las esquinas.",
    ]
    for i, line in enumerate(instructions):
        texts.append(
            Text(info_x, top + 20 + i * 3.6, line, size=7, bold=(i == 0),
                 max_width=info_w, gray=0.2)
        )

    block_bottom = max(sid_bottom, qr_box.y + qr_box.h, top + 20 + len(instructions) * 3.6)
    lines.append(Line(left, block_bottom + 3, right, block_bottom + 3))
    logger.debug("bloque superior termina en y=%.1f mm", block_bottom)

    # --- Preguntas: fluyen por columnas, sección por sección ---
    q_top = block_bottom + 6
    col_inner_w = Q_LABEL_W + 1 + exam.num_options * Q_PITCH_X
    n_cols = int((right - left + Q_COL_GAP) // (col_inner_w + Q_COL_GAP))
    col_pitch = (right - left) / n_cols
    rows_per_col = int((bottom - q_top) // Q_ROW_H)
    logger.info("bloque 4/4: preguntas en %d columnas de hasta %d filas, desde y=%.1f mm",
                n_cols, rows_per_col, q_top)

    # Lista de filas: ("header", Section) o ("question", Question)
    rows: list[tuple[str, object]] = []
    for section in exam.sections:
        rows.append(("header", section))
        rows.extend(("question", q) for q in section.questions)

    # Columnas balanceadas: se busca la altura de columna más corta con la
    # que todo cabe, en vez de llenar la primera hasta abajo.
    placements = None
    for col_height in range(-(-len(rows) // n_cols), rows_per_col + 1):
        placements = _flow_rows(rows, col_height, n_cols)
        if placements is not None:
            break
    if placements is None:
        logger.warning("no cabe: %d filas en %d columnas de %d", len(rows), n_cols, rows_per_col)
        capacity = n_cols * rows_per_col - len(exam.sections)
        raise LayoutError(
            f"El examen no cabe en una hoja: {exam.total_questions} preguntas "
            f"con {exam.num_options} opciones (máximo aprox. {capacity}). "
            "Reduce preguntas u opciones, o divídelo en dos exámenes."
        )
    logger.debug("columnas balanceadas: %d filas por columna (%d filas en total)",
                 col_height, len(rows))

    questions: dict[int, list[Bubble]] = {}
    for (kind, item), (col, row) in zip(rows, placements):
        col_x = left + col * col_pitch
        cy = q_top + row * Q_ROW_H + Q_ROW_H / 2

        if kind == "header":
            section = item
            if section.weight is not None:
                value = f"{section.weight:g}%"
            else:
                value = f"{section.max_points:g} pts"
            texts.append(
                Text(col_x, cy + 1.3, f"{section.name} ({value})", size=8,
                     bold=True, max_width=col_inner_w)
            )
            logger.debug("sección '%s' (%s): columna %d, fila %d", section.id, value, col, row)
        else:
            question = item
            texts.append(
                Text(col_x + Q_LABEL_W - 1, cy + 1.2, str(question.number),
                     size=8, bold=True, align="right")
            )
            first_x = col_x + Q_LABEL_W + 1 + Q_BUBBLE_R
            questions[question.number] = [
                Bubble(first_x + i * Q_PITCH_X, cy, Q_BUBBLE_R, letter)
                for i, letter in enumerate(exam.options)
            ]
            logger.debug("pregunta %d: columna %d, fila %d, y=%.1f mm", question.number, col, row, cy)

    layout = SheetLayout(
        exam_id=exam.id,
        page_size=geo.size,
        marker_size=MARKER_SIZE,
        marker_centers=geo.marker_centers,
        qr_box=qr_box,
        qr_payload=QR_PREFIX + exam.id,
        student_id=student_id,
        questions=questions,
        texts=texts,
        boxes=boxes,
        lines=lines,
    )
    logger.info("fin: %d preguntas, %d textos, %d cuadros, %d líneas; imagen alineada de %dx%d px",
                len(questions), len(texts), len(boxes), len(lines), *layout.canonical_size)
    return layout


def _flow_rows(
    rows: list[tuple[str, object]], col_height: int, n_cols: int
) -> list[tuple[int, int]] | None:
    """Asigna (columna, fila) a cada fila con columnas de `col_height` filas.
    Devuelve None si no caben en `n_cols` columnas."""
    placements = []
    col, row = 0, 0
    for kind, _ in rows:
        # Un encabezado de sección nunca queda solo al final de una columna
        if row >= col_height or (kind == "header" and row >= col_height - 1):
            col, row = col + 1, 0
        if col >= n_cols:
            logger.debug("col_height=%d: no cabe en %d columnas", col_height, n_cols)
            return None
        placements.append((col, row))
        row += 1
    logger.debug("col_height=%d: cabe, usa %d columna(s)", col_height, col + 1)
    return placements
