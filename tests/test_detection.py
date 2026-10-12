"""
Lectura de hojas sintéticas: se generan, se "contestan" por código con
distintos tipos de marca, se deforman como foto de celular y se verifica
que la lectura sea exacta y que lo dudoso quede como ambiguo (nunca
adivinado).
"""

import cv2
import pytest

from core.alignment import align_sheet
from core.detection import read_sheet
from core.layout import build_layout
from core.pipeline import GradingError, grade_photo
from core.sheet_pdf import render
from models.exam import load_exam
from tests.helpers import RASTER_DPI, cross_bubbles, fill_bubbles, rasterize, simulate_photo

EXAM = load_exam("data/examples/ejemplo.json")
LAYOUT = build_layout(EXAM)
BLANK_SHEET = rasterize(render(LAYOUT))
OPTIONS = EXAM.options
STUDENT_ID = "204517"

# Hoja de prueba: cada pregunta con un caso distinto
ANSWERED = {q: OPTIONS[(q * 7) % 4] for q in range(1, 31)}
BLANK = [5, 17]
DOUBLE = [8]  # dos burbujas rellenas
FAINT = [12]  # una sola marca muy tenue
HALF = [20]  # una sola marca rellena a medias
CROSSED = [23]  # tachada con X
ERASED_NEXT_TO_FILLED = [27]  # una borrada (tenue) y otra bien rellena -> se acepta
for q in BLANK + DOUBLE + FAINT + HALF + CROSSED:
    ANSWERED.pop(q)
AMBIGUOUS = sorted(DOUBLE + FAINT + HALF + CROSSED)


def bubble(q, letter):
    return LAYOUT.questions[q][OPTIONS.index(letter)]


def make_sheet(student_id=STUDENT_ID):
    image = BLANK_SHEET.copy()
    for i, d in enumerate(student_id):
        if d != " ":
            fill_bubbles(image, LAYOUT, [LAYOUT.student_id[i][int(d)]])
    fill_bubbles(image, LAYOUT, [bubble(q, a) for q, a in ANSWERED.items()])
    fill_bubbles(image, LAYOUT, [bubble(q, l) for q in DOUBLE for l in "AC"])
    fill_bubbles(image, LAYOUT, [bubble(q, "B") for q in FAINT], gray=170)
    fill_bubbles(image, LAYOUT, [bubble(q, "D") for q in HALF], size=0.45)
    cross_bubbles(image, [bubble(q, "A") for q in CROSSED])
    fill_bubbles(image, LAYOUT, [bubble(q, wrong) for q in ERASED_NEXT_TO_FILLED
                                 for wrong in OPTIONS if wrong != ANSWERED[q]][:1], gray=170)
    return image


def read(photo):
    aligned = align_sheet(photo, size=LAYOUT.canonical_size)
    return read_sheet(aligned, LAYOUT)


@pytest.mark.parametrize("seed", range(6))
def test_reads_every_case_correctly(seed):
    r = read(simulate_photo(make_sheet(), seed=seed))
    assert r.student_id == STUDENT_ID
    for q, letter in ANSWERED.items():
        assert r.answers[q] == letter, f"pregunta {q}: {r.fills[q]}"
    assert r.blank == BLANK
    assert r.ambiguous == AMBIGUOUS
    for q in BLANK + AMBIGUOUS:
        assert r.answers[q] is None
    assert r.confidence > 0.3


def test_scan_without_distortion():
    r = read(make_sheet())
    assert r.student_id == STUDENT_ID
    assert r.ambiguous == AMBIGUOUS


def test_incomplete_student_id_is_not_guessed():
    r = read(simulate_photo(make_sheet(student_id="20 517"), seed=3))
    assert r.student_id is None
    assert r.student_id_raw == "20?517"


def test_wrong_size_alignment_raises():
    with pytest.raises(ValueError, match="canonical_size"):
        read_sheet(align_sheet(make_sheet()), LAYOUT)


def test_pipeline_grades_photo():
    photo = simulate_photo(make_sheet(), seed=2)
    graded = grade_photo(photo, find_exam={EXAM.id: EXAM}.get)
    assert graded.qr_read
    assert graded.reading.student_id == STUDENT_ID
    expected = sum(1 for q, a in ANSWERED.items() if EXAM.question_by_number(q).correct_answer == a)
    assert len(graded.result.correct) == expected
    assert graded.result.needs_review  # tiene ambiguas


def test_pipeline_unknown_exam():
    photo = simulate_photo(make_sheet(), seed=2)
    with pytest.raises(GradingError, match="no está registrado"):
        grade_photo(photo, find_exam=lambda _: None)


def test_pipeline_uses_fallback_when_qr_unreadable():
    sheet = make_sheet()
    px = RASTER_DPI / 25.4
    box = LAYOUT.qr_box
    cv2.rectangle(sheet, (round(box.x * px), round(box.y * px)),
                  (round((box.x + box.w) * px), round((box.y + box.h) * px)), (255, 255, 255), -1)
    photo = simulate_photo(sheet, seed=4)

    with pytest.raises(GradingError, match="QR"):
        grade_photo(photo, find_exam={EXAM.id: EXAM}.get)

    graded = grade_photo(photo, find_exam={EXAM.id: EXAM}.get, fallback_exam_id=EXAM.id)
    assert not graded.qr_read
    assert graded.reading.student_id == STUDENT_ID


@pytest.mark.parametrize("gray", [110, 140])
def test_light_pencil_sheet_is_read(gray):
    """Lápiz suave en toda la hoja (como en las fotos reales, donde una
    marca mide ~0.4-0.6 y no ~0.85): la calibración por hoja debe leerla
    completa en vez de dejar todo como ambiguo."""
    image = BLANK_SHEET.copy()
    fill_bubbles(image, LAYOUT, [LAYOUT.student_id[i][int(d)] for i, d in enumerate(STUDENT_ID)],
                 gray=gray)
    answers = {q: OPTIONS[(q * 7) % 4] for q in range(1, 31)}
    fill_bubbles(image, LAYOUT, [bubble(q, a) for q, a in answers.items()], gray=gray)
    r = read(simulate_photo(image, seed=5))
    assert r.student_id == STUDENT_ID
    assert r.answers == answers
    assert r.ambiguous == [] and r.blank == []


def test_blank_sheet_reads_all_blank():
    r = read(simulate_photo(BLANK_SHEET.copy(), seed=0))
    assert r.student_id is None
    assert sorted(r.blank) == list(range(1, 31))
    assert r.ambiguous == []
