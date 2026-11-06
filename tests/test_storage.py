"""
Capa de BD con SQLite en memoria: mismas tablas que irán a Supabase.

Verifica las reglas del proyecto: borrar examen borra resultados, examen
con resultados no cambia de estructura, y matrícula duplicada -> el
profesor elige.
"""

import copy
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from core.scoring import score
from models.exam import load_exam
from models.result import SheetReading
from storage import repository
from storage.db import init_db, make_engine
from storage.export import results_dataframe
from storage.tables import QuestionRow, ResultRow, SectionRow

EXAM = load_exam("data/examples/ejemplo.json")
CORRECT = {q.number: q.correct_answer for s in EXAM.sections for q in s.questions}


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    init_db(engine)
    with sessionmaker(engine)() as s:
        yield s


def save_scan(session, student_id="204517", answers=None, exam=EXAM):
    answers = dict(CORRECT if answers is None else answers)
    reading = SheetReading(
        exam_id=exam.id,
        student_id=student_id,
        student_id_raw=student_id or "20?517",
        answers={n: answers.get(n) for n in CORRECT},
        blank=[n for n in CORRECT if answers.get(n) is None],
    )
    return repository.add_result(session, score(exam, reading), reading)


def count(session, table):
    return session.scalar(select(func.count()).select_from(table))


def test_exam_roundtrip(session):
    repository.create_exam(session, EXAM)
    assert repository.get_exam(session, EXAM.id) == EXAM
    assert [e.id for e in repository.list_exams(session)] == [EXAM.id]


def test_exam_id_must_be_unique(session):
    repository.create_exam(session, EXAM)
    with pytest.raises(repository.ExamExistsError):
        repository.create_exam(session, EXAM)


def test_other_owner_cannot_see_exam(session):
    repository.create_exam(session, EXAM)
    other = uuid.uuid4()
    assert repository.get_exam(session, EXAM.id, owner_id=other) is None
    assert repository.list_exams(session, owner_id=other) == []
    with pytest.raises(repository.ExamNotFoundError):
        repository.delete_exam(session, EXAM.id, owner_id=other)


def test_delete_exam_deletes_results(session):
    repository.create_exam(session, EXAM)
    save_scan(session, "111111")
    save_scan(session, "222222")
    assert repository.delete_exam(session, EXAM.id) == 2
    assert repository.get_exam(session, EXAM.id) is None
    for table in (ResultRow, SectionRow, QuestionRow):
        assert count(session, table) == 0


def test_structure_change_allowed_without_results(session):
    repository.create_exam(session, EXAM)
    changed = copy.deepcopy(EXAM)
    changed.num_options = 5
    repository.update_exam(session, changed)
    assert repository.get_exam(session, EXAM.id).num_options == 5


@pytest.mark.parametrize("mutate", [
    lambda e: setattr(e, "num_options", 5),
    lambda e: setattr(e, "student_id_digits", 7),
    lambda e: e.sections[0].questions.pop(),
    lambda e: e.sections.reverse(),
])
def test_structure_locked_with_results(session, mutate):
    repository.create_exam(session, EXAM)
    save_scan(session)
    changed = copy.deepcopy(EXAM)
    mutate(changed)
    with pytest.raises(repository.ExamLockedError, match="no se puede cambiar"):
        repository.update_exam(session, changed)
    assert repository.get_exam(session, EXAM.id) == EXAM


def test_answer_key_change_rescores(session):
    repository.create_exam(session, EXAM)
    row, _ = save_scan(session)
    assert row.final_score == 100

    changed = copy.deepcopy(EXAM)
    q1 = changed.sections[0].questions[0]
    q1.correct_answer = "A" if q1.correct_answer != "A" else "B"
    changed.name = "Nombre nuevo"
    assert repository.update_exam(session, changed) == 1

    rescored = repository.get_result(session, row.id)
    assert rescored.final_score < 100
    assert repository.get_exam(session, EXAM.id).name == "Nombre nuevo"


def test_duplicate_student_waits_for_teacher(session):
    repository.create_exam(session, EXAM)
    first, others = save_scan(session, "204517")
    assert first.selected and others == []

    second, others = save_scan(session, "204517", answers={})
    assert [o.id for o in others] == [first.id]
    assert not first.selected and not second.selected
    assert list(repository.pending_duplicates(session, EXAM.id)) == ["204517"]

    repository.choose_result(session, second.id)
    assert second.selected and not first.selected
    assert repository.pending_duplicates(session, EXAM.id) == {}

    # Un tercer escaneo vuelve a dejarla pendiente
    third, others = save_scan(session, "204517")
    assert len(others) == 2
    assert not any(r.selected for r in repository.list_results(session, EXAM.id))


def test_unreadable_student_ids_are_not_duplicates(session):
    repository.create_exam(session, EXAM)
    a, _ = save_scan(session, None)
    b, others = save_scan(session, None)
    assert others == [] and a.selected and b.selected


def test_export_one_row_per_student(session):
    repository.create_exam(session, EXAM)
    save_scan(session, "111111")
    save_scan(session, "222222", answers={})
    save_scan(session, "222222")
    save_scan(session, None)

    df = results_dataframe(session, EXAM.id)
    by_id = df.set_index("matricula")
    assert by_id.loc["111111", "nota"] == 100
    assert by_id.loc["222222", "estado"].startswith("elegir resultado")
    assert by_id.loc["20?517", "estado"] == "revisar matrícula"
    assert len(df) == 3

    chosen = [r for r in repository.list_results(session, EXAM.id) if r.student_id == "222222"][0]
    repository.choose_result(session, chosen.id)
    df = results_dataframe(session, EXAM.id).set_index("matricula")
    assert df.loc["222222", "nota"] == chosen.final_score


# --- Corrección a mano ---

def save_ambiguous(session, student_id="204517", ambiguous=(1, 2)):
    """Hoja con todo bien salvo `ambiguous`, que el lector no pudo decidir."""
    reading = SheetReading(
        exam_id=EXAM.id, student_id=student_id, student_id_raw=student_id or "20?517",
        answers={n: (None if n in ambiguous else a) for n, a in CORRECT.items()},
        ambiguous=list(ambiguous),
    )
    return repository.add_result(session, score(EXAM, reading), reading)[0]


def test_correct_ambiguous_questions(session):
    repository.create_exam(session, EXAM)
    row = save_ambiguous(session)
    assert row.needs_review and row.final_score < 100

    repository.correct_result(session, row, EXAM, {1: CORRECT[1]})
    assert row.ambiguous == [2] and row.needs_review
    repository.correct_result(session, row, EXAM, {2: None})  # en blanco
    assert row.ambiguous == [] and row.blank == [2] and not row.needs_review
    assert row.answers["1"] == CORRECT[1] and row.answers["2"] is None
    assert row.final_score == score(EXAM, repository.row_to_reading(row)).final_score < 100


def test_correction_survives_answer_key_change(session):
    repository.create_exam(session, EXAM)
    row = save_ambiguous(session, ambiguous=(1,))
    repository.correct_result(session, row, EXAM, {1: CORRECT[1]})
    assert row.final_score == 100

    changed = copy.deepcopy(EXAM)
    changed.sections[0].questions[1].correct_answer = "A" if CORRECT[2] != "A" else "B"
    repository.update_exam(session, changed)
    row = repository.get_result(session, row.id)
    assert row.answers["1"] == CORRECT[1]  # la corrección sigue ahí
    assert 0 < row.final_score < 100  # solo bajó por la pregunta 2


def test_correct_student_id(session):
    repository.create_exam(session, EXAM)
    unreadable = save_ambiguous(session, student_id=None, ambiguous=())
    assert unreadable.needs_review
    repository.correct_result(session, unreadable, EXAM, student_id="204517")
    assert unreadable.student_id == unreadable.student_id_raw == "204517"
    assert unreadable.selected and not unreadable.needs_review

    # Si la matrícula ya existe, quedan las dos por elegir
    other = save_ambiguous(session, student_id="111111", ambiguous=())
    repository.correct_result(session, other, EXAM, student_id="204517")
    assert not other.selected and not unreadable.selected
    assert list(repository.pending_duplicates(session, EXAM.id)) == ["204517"]


@pytest.mark.parametrize("answers, student_id, text", [
    ({999: "A"}, None, "no tiene la pregunta 999"),
    ({1: "E"}, None, "no es una opción"),
    ({}, "12345", "6 dígitos"),
    ({}, "12345a", "6 dígitos"),
])
def test_invalid_correction(session, answers, student_id, text):
    repository.create_exam(session, EXAM)
    row = save_ambiguous(session)
    with pytest.raises(repository.ResultValidationError, match=text):
        repository.correct_result(session, row, EXAM, answers, student_id)


def test_moving_out_of_duplicate_selects_the_other(session):
    repository.create_exam(session, EXAM)
    a, _ = save_scan(session, "204517")
    b, _ = save_scan(session, "204517", answers={})
    assert not a.selected and not b.selected
    repository.correct_result(session, b, EXAM, student_id="999999")  # era otro alumno
    assert a.selected and b.selected
    assert repository.pending_duplicates(session, EXAM.id) == {}


def test_delete_duplicate_selects_the_one_left(session):
    repository.create_exam(session, EXAM)
    a, _ = save_scan(session, "204517")
    b, _ = save_scan(session, "204517")
    repository.delete_result(session, b)
    assert [r.id for r in repository.list_results(session, EXAM.id)] == [a.id]
    assert a.selected
