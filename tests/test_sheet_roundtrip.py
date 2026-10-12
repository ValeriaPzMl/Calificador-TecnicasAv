"""
Ida y vuelta completa de la fase 1: examen -> layout -> PDF -> "foto"
deformada -> alineación -> las burbujas están donde dice el layout.

Si esta prueba pasa, el PDF y el detector comparten coordenadas, que es
la base de la fase 2.
"""

import pytest

from core.alignment import align_sheet
from core.layout import build_layout
from core.preprocessing import to_grayscale
from core.qr import read_exam_id
from core.sheet_pdf import render
from models.exam import load_exam
from tests.helpers import darkness, fill_bubbles, rasterize, simulate_photo

EXAM = load_exam("data/examples/ejemplo.json")
LAYOUT = build_layout(EXAM)
PDF = render(LAYOUT)

STUDENT_ID = "204517"
ANSWERS = {q: "ABCD"[(q * 7) % 4] for q in LAYOUT.questions}


def answered_sheet():
    image = rasterize(PDF)
    to_fill = [LAYOUT.student_id[i][int(d)] for i, d in enumerate(STUDENT_ID)]
    for q, letter in ANSWERS.items():
        to_fill.append(LAYOUT.questions[q][EXAM.options.index(letter)])
    fill_bubbles(image, LAYOUT, to_fill)
    return image


def test_pdf_is_valid():
    assert PDF.startswith(b"%PDF")


@pytest.mark.parametrize("seed", range(5))
def test_filled_bubbles_land_where_layout_says(seed):
    photo = simulate_photo(answered_sheet(), seed=seed)
    aligned = to_grayscale(align_sheet(photo, size=LAYOUT.canonical_size))
    assert aligned.shape[::-1] == LAYOUT.canonical_size

    for q, bubbles in LAYOUT.questions.items():
        for b in bubbles:
            d = darkness(aligned, LAYOUT, b)
            if b.value == ANSWERS[q]:
                assert d > 0.6, f"pregunta {q}{b.value} debería verse rellena ({d:.2f})"
            else:
                assert d < 0.35, f"pregunta {q}{b.value} debería verse vacía ({d:.2f})"

    for i, row in enumerate(LAYOUT.student_id):
        darkest = max(row, key=lambda b: darkness(aligned, LAYOUT, b))
        assert darkest.value == STUDENT_ID[i]


@pytest.mark.parametrize("seed", range(10))
def test_qr_identifies_exam(seed):
    photo = simulate_photo(rasterize(PDF), seed=seed)
    assert read_exam_id(photo) == EXAM.id


def test_qr_on_a4_sheet():
    layout = build_layout(EXAM, page="a4")
    photo = simulate_photo(rasterize(render(layout)), seed=1)
    assert read_exam_id(photo) == EXAM.id
