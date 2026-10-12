"""
Lectura de la hoja: imagen alineada + SheetLayout -> SheetReading.

Como el layout dice exactamente dónde está cada burbuja, aquí no se busca
nada: solo se mide qué tan oscura está cada una.

1. Medida de una burbuja ("fill", 0 = papel, 1 = negro):
       1 - (gris promedio del centro de la burbuja) / (gris del papel alrededor)
   El papel se mide alrededor de cada burbuja (percentil alto de un cuadro
   vecino), así funciona con sombras e iluminación despareja. Se mide solo
   el centro (FILL_MASK_RATIO del radio) para que el contorno impreso no
   cuente.

2. Calibración por hoja: los valores absolutos cambian mucho entre lápiz,
   pluma y cámara (lápiz real en foto de celular: ~0.43-0.63; relleno
   negro sintético: ~0.85). Por eso cada hoja se calibra con sus propias
   burbujas:
   - `empty_level`: cómo se ve una burbuja vacía (percentil 25 de todas;
     la mayoría están vacías). Típico: ~0.08-0.12 (la letra impresa).
   - `mark_level`: cómo marca este alumno (mediana de la burbuja más
     oscura de cada fila, entre las filas con alguna marca clara).
   Cada fill se normaliza: 0 = vacía, 1 = marca típica de esta hoja.

3. Regla por pregunta (y por dígito de matrícula), en valores normalizados:
   - Rellena: >= FILLED_REL. Vacía: < BLANK_REL. En medio: "dudosa".
   - Contestada solo si hay **una** rellena y destaca sobre la segunda por
     al menos MIN_MARGIN_REL. Una burbuja borrada junto a otra bien
     rellena no estorba.
   - Ninguna rellena ni dudosa -> en blanco.
   - Cualquier otro caso (dos rellenas, una marca mucho más tenue que las
     demás de la hoja, X, a medias...) -> ambigua. **Nunca se adivina.**
"""

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from models.result import SheetReading

from .layout import Bubble, SheetLayout
from .preprocessing import to_grayscale

logger = logging.getLogger(__name__)

# Normalizados: 0 = burbuja vacía, 1 = marca típica de la hoja.
# Medido en 8 fotos reales (celular, lápiz): marca más suave 0.60, mediana
# 1.0; vacías hasta 0.20 (p99); dobles marcas 0.56-0.83; una tachada 0.50.
FILLED_REL = 0.55
BLANK_REL = 0.25
MIN_MARGIN_REL = 0.35

# Una fila cuenta como "con marca clara" para calibrar si su burbuja más
# oscura supera a las vacías por esto (en fill absoluto). Si ninguna fila
# lo logra, la hoja está en blanco.
MIN_CONTRAST = 0.15
EMPTY_PERCENTILE = 25

FILL_MASK_RATIO = 0.65  # parte del radio que se mide
PAPER_PATCH_RATIO = 2.2  # medio lado del cuadro donde se mide el papel, en radios
PAPER_PERCENTILE = 90

ANSWERED, BLANK, AMBIGUOUS = "answered", "blank", "ambiguous"


@dataclass(frozen=True)
class Calibration:
    empty_level: float
    mark_level: float | None  # None = no hay ninguna marca clara en la hoja

    def normalize(self, fill: float) -> float:
        if self.mark_level is None:
            return 0.0
        return (fill - self.empty_level) / (self.mark_level - self.empty_level)


def bubble_fill(gray: np.ndarray, layout: SheetLayout, bubble: Bubble) -> float:
    """Oscuridad (0-1) del centro de una burbuja, relativa al papel de alrededor."""
    cx, cy, r = layout.bubble_px(bubble)
    half = int(r * PAPER_PATCH_RATIO)
    x0, y0 = max(0, int(cx) - half), max(0, int(cy) - half)
    patch = gray[y0:int(cy) + half, x0:int(cx) + half]
    paper = float(np.percentile(patch, PAPER_PERCENTILE)) if patch.size else 255.0

    mask = np.zeros(gray.shape, np.uint8)
    cv2.circle(mask, (round(cx), round(cy)), max(1, round(r * FILL_MASK_RATIO)), 255, -1)
    inner = cv2.mean(gray, mask=mask)[0]
    fill = float(np.clip(1 - inner / max(paper, 1.0), 0.0, 1.0))
    logger.debug("burbuja '%s' en (%.0f, %.0f) px: centro %.0f, papel %.0f -> fill %.2f",
                 bubble.value, cx, cy, inner, paper, fill)
    return fill


def calibrate(rows: list[list[float]]) -> Calibration:
    """Calcula cómo se ven una burbuja vacía y una marca en esta hoja."""
    all_fills = np.concatenate([np.asarray(r) for r in rows])
    empty = float(np.percentile(all_fills, EMPTY_PERCENTILE))
    marked = [max(r) for r in rows if max(r) - empty >= MIN_CONTRAST]
    mark = float(np.median(marked)) if marked else None
    calib = Calibration(empty, mark)
    if mark is None:
        logger.warning("ninguna burbuja destaca sobre las vacías (%.2f): hoja en blanco", empty)
    else:
        logger.info("calibración: vacía=%.2f, marca típica=%.2f (de %d filas con marca)",
                    empty, mark, len(marked))
    return calib


def classify(fills: list[float], values: list[str],
             calib: Calibration) -> tuple[str, str | None, float]:
    """Decide qué marcó el alumno en una fila de burbujas.

    Devuelve (estado, valor elegido o None, margen normalizado sobre la segunda).
    """
    rel = [calib.normalize(f) for f in fills]
    order = sorted(range(len(rel)), key=lambda i: rel[i], reverse=True)
    best, second = rel[order[0]], (rel[order[1]] if len(order) > 1 else 0.0)
    margin = best - second
    filled = sum(r >= FILLED_REL for r in rel)
    doubtful = sum(BLANK_REL <= r < FILLED_REL for r in rel)

    if filled == 1 and margin >= MIN_MARGIN_REL:
        state, value = ANSWERED, values[order[0]]
    elif filled == 0 and doubtful == 0:
        state, value = BLANK, None
    else:
        state, value = AMBIGUOUS, None
    logger.debug("fills %s -> normalizados %s -> %s %s (rellenas=%d, dudosas=%d, margen=%.2f)",
                 [round(f, 2) for f in fills], [round(r, 2) for r in rel],
                 state, value or "", filled, doubtful, margin)
    return state, value, margin


def read_sheet(aligned: np.ndarray, layout: SheetLayout, exam_id: str | None = None) -> SheetReading:
    """Lee matrícula y respuestas de una hoja ya alineada con
    `alignment.align_sheet(..., size=layout.canonical_size)`."""
    logger.info("inicio: leyendo hoja de %dx%d px con el layout de '%s'",
                aligned.shape[1], aligned.shape[0], layout.exam_id)
    expected = layout.canonical_size
    if (aligned.shape[1], aligned.shape[0]) != expected:
        raise ValueError(
            f"La imagen alineada mide {aligned.shape[1]}x{aligned.shape[0]} px y el "
            f"layout espera {expected[0]}x{expected[1]}. Alinea con size=layout.canonical_size."
        )
    gray = to_grayscale(aligned)

    logger.info("paso 1/3: midiendo %d burbujas", len(layout.all_bubbles()))
    sid_fills = [[bubble_fill(gray, layout, b) for b in row] for row in layout.student_id]
    numbers = sorted(layout.questions)
    q_fills = {n: [bubble_fill(gray, layout, b) for b in layout.questions[n]] for n in numbers}

    logger.info("paso 2/3: calibrando con las marcas de esta hoja")
    calib = calibrate(sid_fills + list(q_fills.values()))
    margins: list[float] = []

    logger.info("paso 3/3: matrícula (%d dígitos) y %d preguntas", len(sid_fills), len(numbers))
    digits: list[str] = []
    for pos, (row, fills) in enumerate(zip(layout.student_id, sid_fills)):
        state, value, margin = classify(fills, [b.value for b in row], calib)
        digits.append(value if state == ANSWERED else "?")
        if state == ANSWERED:
            margins.append(margin)
        logger.debug("dígito %d: %s", pos + 1, value if state == ANSWERED else state)
    raw = "".join(digits)
    student_id = None if "?" in raw else raw
    if student_id is None:
        logger.warning("matrícula incompleta: %s", raw)

    answers: dict[int, str | None] = {}
    ambiguous: list[int] = []
    blank: list[int] = []
    all_fills: dict[int, list[float]] = {}
    for number in numbers:
        fills = q_fills[number]
        all_fills[number] = [round(f, 3) for f in fills]
        state, value, margin = classify(fills, [b.value for b in layout.questions[number]], calib)
        answers[number] = value
        if state == AMBIGUOUS:
            ambiguous.append(number)
            logger.info("pregunta %d ambigua: fills %s", number, all_fills[number])
        elif state == BLANK:
            blank.append(number)
        else:
            margins.append(margin)
        logger.debug("pregunta %d: %s", number, value or state)

    # Confianza: el menor margen entre la elegida y la segunda (0-1)
    confidence = round(min(1.0, min(margins)), 3) if margins else 0.0
    reading = SheetReading(
        exam_id=exam_id or layout.exam_id,
        student_id=student_id,
        student_id_raw=raw,
        answers=answers,
        ambiguous=ambiguous,
        blank=blank,
        fills=all_fills,
        confidence=confidence,
    )
    logger.info("fin: matrícula %s, %d contestadas, %d en blanco, %d ambiguas, confianza %.2f",
                raw, len(answers) - len(blank) - len(ambiguous), len(blank),
                len(ambiguous), confidence)
    return reading


def draw_reading(aligned: np.ndarray, layout: SheetLayout, reading: SheetReading,
                 correct_answers: dict[int, str] | None = None) -> np.ndarray:
    """Imagen para que el profesor revise la lectura.

    Por pregunta: verde = la que se leyó (y es correcta, si se pasan
    `correct_answers`), rojo = leída pero incorrecta, naranja = ambigua
    (todas sus burbujas), gris = en blanco.
    """
    logger.info("dibujando revisión de la lectura")
    out = aligned.copy() if aligned.ndim == 3 else cv2.cvtColor(aligned, cv2.COLOR_GRAY2BGR)
    green, red, orange, gray = (0, 170, 0), (0, 0, 220), (0, 140, 255), (160, 160, 160)

    def circle(b: Bubble, color, thickness=2):
        cx, cy, r = layout.bubble_px(b)
        cv2.circle(out, (round(cx), round(cy)), round(r) + 2, color, thickness)

    for pos, row in enumerate(layout.student_id):
        digit = reading.student_id_raw[pos] if pos < len(reading.student_id_raw) else "?"
        for b in row:
            if digit == "?":
                circle(b, orange)
            elif b.value == digit:
                circle(b, green)

    for number, bubbles in layout.questions.items():
        answer = reading.answers.get(number)
        for b in bubbles:
            if number in reading.ambiguous:
                circle(b, orange)
            elif number in reading.blank:
                circle(b, gray, 1)
            elif b.value == answer:
                ok = correct_answers is None or correct_answers.get(number) == answer
                circle(b, green if ok else red)
            elif correct_answers and correct_answers.get(number) == b.value:
                circle(b, green, 1)  # la correcta, que el alumno no marcó
    return out
