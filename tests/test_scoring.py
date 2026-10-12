"""Calificación con casos calculados a mano (sin imágenes)."""

import pytest

from core.scoring import score
from models.exam import Exam, Question, Section, load_exam
from models.result import SheetReading

EXAM = load_exam("data/examples/ejemplo.json")  # pesos 40 / 35 / 25
CORRECT = {q.number: q.correct_answer for s in EXAM.sections for q in s.questions}


def wrong(letter: str) -> str:
    return "A" if letter != "A" else "B"


def reading(answers, ambiguous=(), student_id="204517") -> SheetReading:
    blank = [n for n in CORRECT if answers.get(n) is None and n not in ambiguous]
    return SheetReading(
        exam_id=EXAM.id,
        student_id=student_id,
        student_id_raw=student_id or "20?517",
        answers={n: answers.get(n) for n in CORRECT},
        ambiguous=list(ambiguous),
        blank=blank,
    )


def test_all_correct_is_100():
    result = score(EXAM, reading(CORRECT))
    assert result.final_score == 100
    assert result.max_score == 100
    assert sorted(result.correct) == sorted(CORRECT)
    assert not result.needs_review


def test_all_blank_is_0():
    result = score(EXAM, reading({}))
    assert result.final_score == 0
    assert sorted(result.blank) == sorted(CORRECT)


def test_weighted_by_section():
    algebra = {q.number for q in EXAM.sections[0].questions}
    answers = {n: (a if n in algebra else wrong(a)) for n, a in CORRECT.items()}
    result = score(EXAM, reading(answers))
    assert result.final_score == 40  # álgebra completa vale 40%
    assert [s.percent for s in result.sections] == [100, 0, 0]


def test_points_inside_section_are_weighted():
    # Geometría (35%) tiene 15 puntos; acertar solo la primera pregunta da
    # sus puntos / 15 * 35
    geo = EXAM.sections[1]
    first = geo.questions[0]
    result = score(EXAM, reading({first.number: first.correct_answer}))
    assert result.final_score == pytest.approx(round(first.points / geo.max_points * 35, 2))


def test_without_weights_is_sum_of_points():
    exam = Exam(name="Sin pesos", id="sinpesos", sections=[
        Section("a", "A", [Question(1, "A", 2), Question(2, "B", 3)]),
        Section("b", "B", [Question(3, "C", 5)]),
    ])
    r = SheetReading(exam.id, "1", "1", {1: "A", 2: "C", 3: "C"})
    result = score(exam, r)
    assert result.final_score == 7
    assert result.max_score == 10


def test_ambiguous_counts_zero_and_needs_review():
    n = next(iter(CORRECT))
    answers = {k: v for k, v in CORRECT.items() if k != n}
    result = score(EXAM, reading(answers, ambiguous=[n]))
    assert n not in result.correct
    assert result.ambiguous == [n]
    assert result.final_score < 100
    assert result.needs_review


def test_unreadable_student_id_needs_review():
    result = score(EXAM, reading(CORRECT, student_id=None))
    assert result.final_score == 100
    assert result.needs_review
