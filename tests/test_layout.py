import itertools

import pytest

from core.layout import LayoutError, build_layout
from models.exam import Exam, ExamValidationError, Question, Section, load_exam

EXAMPLE = "data/examples/ejemplo.json"


def make_exam(n_questions: int, num_options: int = 4, sections: int = 1) -> Exam:
    per_section = n_questions // sections
    secs = []
    number = 1
    for s in range(sections):
        qs = [Question(number + i, "A") for i in range(per_section)]
        number += per_section
        secs.append(Section(id=f"s{s}", name=f"Sección {s + 1}", questions=qs))
    return Exam(name="Prueba", sections=secs, num_options=num_options)


def test_example_exam_loads():
    exam = load_exam(EXAMPLE)
    assert exam.total_questions == 30
    assert [s.weight for s in exam.sections] == [40, 35, 25]


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda e: setattr(e.sections[0].questions[0], "correct_answer", "E"), "no está en"),
        (lambda e: setattr(e.sections[1].questions[0], "number", 1), "repetida"),
        (lambda e: setattr(e.sections[0], "weight", 50), "suman"),
        (lambda e: setattr(e.sections[0], "weight", None), "todas deben"),
        (lambda e: setattr(e, "num_options", 1), "num_options"),
    ],
)
def test_validation_catches_errors(mutate, message):
    exam = load_exam(EXAMPLE)
    mutate(exam)
    with pytest.raises(ExamValidationError, match=message):
        exam.validate()


def test_every_question_has_one_bubble_per_option():
    exam = load_exam(EXAMPLE)
    layout = build_layout(exam)
    assert sorted(layout.questions) == list(range(1, 31))
    for bubbles in layout.questions.values():
        assert [b.value for b in bubbles] == exam.options
    assert len(layout.student_id) == exam.student_id_digits
    assert all([b.value for b in row] == list("0123456789") for row in layout.student_id)


@pytest.mark.parametrize("n, options", [(30, 4), (90, 4), (40, 6), (24, 8)])
def test_readable_elements_inside_aligned_image_and_no_overlap(n, options):
    layout = build_layout(make_exam(n, options, sections=3))
    w, h = layout.canonical_size
    bubbles = layout.all_bubbles()

    for b in bubbles:
        cx, cy, r = layout.bubble_px(b)
        assert r <= cx <= w - 1 - r and r <= cy <= h - 1 - r, b

    qr = layout.qr_box
    x0, y0 = layout.to_px(qr.x, qr.y)
    x1, y1 = layout.to_px(qr.x + qr.w, qr.y + qr.h)
    assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h

    for a, b in itertools.combinations(bubbles, 2):
        dist = ((a.cx - b.cx) ** 2 + (a.cy - b.cy) ** 2) ** 0.5
        assert dist > a.r + b.r, (a, b)


def test_too_many_questions_raises():
    with pytest.raises(LayoutError, match="no cabe"):
        build_layout(make_exam(200))


def test_a4_page_also_works():
    layout = build_layout(load_exam(EXAMPLE), page="a4")
    assert layout.page_size == (210.0, 297.0)
