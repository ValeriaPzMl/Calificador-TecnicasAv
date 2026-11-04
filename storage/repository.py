"""
Operaciones sobre la BD: lo que van a usar el CLI ahora y la API después.

Convierte entre los modelos (`Exam`, `Result`) y las tablas, y aplica las
reglas del proyecto:

1. **Borrar un examen borra sus resultados.** `delete_exam` regresa cuántos
   se borraron; la confirmación al usuario la pide quien llama (web / CLI).
2. **Un examen con resultados no puede cambiar su estructura** (opciones,
   dígitos de matrícula, qué preguntas hay en cada sección), porque las
   hojas ya impresas dejarían de coincidir. Sí se pueden cambiar
   respuestas correctas, puntos, pesos y nombres; al hacerlo se
   recalifican los resultados guardados.
3. **Matrícula escaneada dos veces:** se guardan todos los resultados y
   ninguno queda `selected` hasta que el profesor elija uno con
   `choose_result`.

Cada función hace commit al terminar.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.exam import Exam, Question, Section
from models.result import Result, SheetReading

from .db import default_owner_id
from .tables import ExamRow, QuestionRow, ResultRow, SectionRow

logger = logging.getLogger(__name__)


class StorageError(Exception):
    pass


class ExamNotFoundError(StorageError):
    pass


class ExamExistsError(StorageError):
    pass


class ExamLockedError(StorageError):
    """Se intentó cambiar la estructura de un examen que ya tiene resultados."""


class ResultNotFoundError(StorageError):
    pass


@dataclass
class ExamSummary:
    id: str
    title: str
    questions: int
    results: int
    created_at: datetime


# --- Exámenes ---
# Las funciones que crean o listan usan `default_owner_id()` si no se pasa
# owner_id (así funciona el CLI). Las que buscan un examen por id no
# filtran por dueño si owner_id es None; la API siempre lo pasa.

def create_exam(session: Session, exam: Exam, owner_id: uuid.UUID | None = None) -> None:
    owner_id = owner_id or default_owner_id()
    logger.info("inicio: guardando examen '%s' de %s", exam.id, owner_id)
    exam.validate()
    if session.get(ExamRow, exam.id) is not None:
        raise ExamExistsError(f"Ya existe un examen con id '{exam.id}'; usa otro id")
    row = ExamRow(id=exam.id, owner_id=owner_id)
    _fill_exam_row(row, exam)
    session.add(row)
    session.commit()
    logger.info("fin: examen '%s' guardado (%d secciones, %d preguntas)",
                exam.id, len(exam.sections), exam.total_questions)


def get_exam(session: Session, exam_id: str, owner_id: uuid.UUID | None = None) -> Exam | None:
    """El examen, o None si no existe (o es de otro usuario, si se pasa owner_id)."""
    logger.debug("buscando examen '%s'", exam_id)
    row = _exam_row(session, exam_id, owner_id)
    if row is None:
        logger.info("examen '%s' no encontrado", exam_id)
        return None
    exam = _row_to_exam(row)
    logger.info("examen '%s' encontrado: %s, %d preguntas", exam.id, exam.name, exam.total_questions)
    return exam


def list_exams(session: Session, owner_id: uuid.UUID | None = None) -> list[ExamSummary]:
    owner_id = owner_id or default_owner_id()
    logger.info("listando exámenes de %s", owner_id)
    rows = session.scalars(
        select(ExamRow).where(ExamRow.owner_id == owner_id).order_by(ExamRow.created_at)
    ).all()
    summaries = [
        ExamSummary(
            id=r.id,
            title=r.title,
            questions=sum(len(s.questions) for s in r.sections),
            results=count_results(session, r.id),
            created_at=r.created_at,
        )
        for r in rows
    ]
    logger.info("%d exámenes", len(summaries))
    return summaries


def update_exam(session: Session, exam: Exam, owner_id: uuid.UUID | None = None) -> int:
    """Reemplaza el examen guardado con `exam` (mismo id).

    Regresa cuántos resultados se recalificaron. Lanza ExamLockedError si
    el examen tiene resultados y el cambio mueve burbujas en la hoja.
    """
    logger.info("inicio: actualizando examen '%s'", exam.id)
    exam.validate()
    row = _exam_row(session, exam.id, owner_id)
    if row is None:
        raise ExamNotFoundError(f"No existe el examen '{exam.id}'")

    old = _row_to_exam(row)
    n_results = count_results(session, exam.id)
    if n_results and old.structure() != exam.structure():
        logger.warning("examen '%s' bloqueado: tiene %d resultados y cambia la estructura",
                       exam.id, n_results)
        raise ExamLockedError(
            f"El examen '{exam.id}' ya tiene {n_results} resultado(s) registrados: no se "
            "puede cambiar el número de opciones, los dígitos de matrícula ni qué "
            "preguntas hay en cada sección, porque las hojas impresas dejarían de "
            "coincidir. Sí puedes cambiar respuestas correctas, puntos, pesos y nombres."
        )

    logger.debug("reemplazando secciones y preguntas")
    row.sections.clear()
    session.flush()
    _fill_exam_row(row, exam)
    session.flush()

    rescored = _rescore_results(session, exam) if n_results else 0
    session.commit()
    logger.info("fin: examen '%s' actualizado, %d resultados recalificados", exam.id, rescored)
    return rescored


def delete_exam(session: Session, exam_id: str, owner_id: uuid.UUID | None = None) -> int:
    """Borra el examen con todos sus resultados. Regresa cuántos resultados
    se borraron. Quien llama debe pedir confirmación antes."""
    logger.info("inicio: borrando examen '%s'", exam_id)
    row = _exam_row(session, exam_id, owner_id)
    if row is None:
        raise ExamNotFoundError(f"No existe el examen '{exam_id}'")
    n_results = count_results(session, exam_id)
    session.delete(row)
    session.commit()
    logger.info("fin: examen '%s' borrado junto con %d resultados", exam_id, n_results)
    return n_results


def count_results(session: Session, exam_id: str) -> int:
    n = session.scalar(
        select(func.count()).select_from(ResultRow).where(ResultRow.exam_id == exam_id)
    ) or 0
    logger.debug("examen '%s' tiene %d resultados", exam_id, n)
    return n


# --- Resultados ---

def add_result(
    session: Session,
    result: Result,
    reading: SheetReading,
    owner_id: uuid.UUID | None = None,
    image_path: str | None = None,
) -> tuple[ResultRow, list[ResultRow]]:
    """Guarda un resultado. Regresa (fila nueva, otros resultados de la
    misma matrícula). Si la lista no está vacía, ninguno queda `selected`
    hasta que el profesor elija con `choose_result`."""
    owner_id = owner_id or default_owner_id()
    logger.info("inicio: guardando resultado de matrícula %s en examen '%s'",
                result.student_id or result.student_id_raw, result.exam_id)
    if session.get(ExamRow, result.exam_id) is None:
        raise ExamNotFoundError(f"No existe el examen '{result.exam_id}'")

    others: list[ResultRow] = []
    if result.student_id is not None:
        others = list(session.scalars(
            select(ResultRow)
            .where(ResultRow.exam_id == result.exam_id, ResultRow.student_id == result.student_id)
            .order_by(ResultRow.scanned_at)
        ))
    if others:
        logger.warning("matrícula %s ya tenía %d resultado(s): quedan todos sin elegir",
                       result.student_id, len(others))
        for other in others:
            other.selected = False
        session.flush()

    row = ResultRow(
        exam_id=result.exam_id,
        owner_id=owner_id,
        selected=not others,
        image_path=image_path,
    )
    _fill_result_row(row, result, reading)
    session.add(row)
    session.commit()
    logger.info("fin: resultado %s guardado (nota %g/%g, selected=%s)",
                row.id, row.final_score, row.max_score, row.selected)
    return row, others


def list_results(session: Session, exam_id: str, only_selected: bool = False) -> list[ResultRow]:
    logger.info("listando resultados del examen '%s'%s", exam_id,
                " (solo elegidos)" if only_selected else "")
    query = select(ResultRow).where(ResultRow.exam_id == exam_id)
    if only_selected:
        query = query.where(ResultRow.selected)
    rows = list(session.scalars(query.order_by(ResultRow.student_id, ResultRow.scanned_at)))
    logger.info("%d resultados", len(rows))
    return rows


def pending_duplicates(session: Session, exam_id: str) -> dict[str, list[ResultRow]]:
    """Matrículas con más de un resultado y ninguno elegido todavía."""
    logger.info("buscando matrículas duplicadas sin elegir en '%s'", exam_id)
    groups: dict[str, list[ResultRow]] = {}
    for row in list_results(session, exam_id):
        if row.student_id is not None:
            groups.setdefault(row.student_id, []).append(row)
    pending = {
        sid: rows for sid, rows in groups.items()
        if len(rows) > 1 and not any(r.selected for r in rows)
    }
    logger.info("%d matrículas por elegir: %s", len(pending), sorted(pending))
    return pending


def choose_result(session: Session, result_id: uuid.UUID) -> ResultRow:
    """El profesor elige este resultado para su matrícula; los demás de la
    misma matrícula quedan como no elegidos."""
    logger.info("inicio: eligiendo resultado %s", result_id)
    row = session.get(ResultRow, result_id)
    if row is None:
        raise ResultNotFoundError(f"No existe el resultado {result_id}")
    if row.student_id is not None:
        others = session.scalars(
            select(ResultRow).where(
                ResultRow.exam_id == row.exam_id,
                ResultRow.student_id == row.student_id,
                ResultRow.id != row.id,
            )
        ).all()
        for other in others:
            other.selected = False
        session.flush()
        logger.debug("%d resultados de la misma matrícula quedan sin elegir", len(others))
    row.selected = True
    session.commit()
    logger.info("fin: resultado %s elegido para matrícula %s", row.id, row.student_id)
    return row


def get_result(session: Session, result_id: uuid.UUID) -> ResultRow | None:
    logger.debug("buscando resultado %s", result_id)
    return session.get(ResultRow, result_id)


def set_image_path(session: Session, result_id: uuid.UUID, image_path: str) -> None:
    logger.debug("resultado %s: imagen de revisión en %s", result_id, image_path)
    row = session.get(ResultRow, result_id)
    if row is None:
        raise ResultNotFoundError(f"No existe el resultado {result_id}")
    row.image_path = image_path
    session.commit()


def row_to_reading(row: ResultRow) -> SheetReading:
    """Reconstruye la lectura guardada (para recalificar sin volver a
    escanear)."""
    return SheetReading(
        exam_id=row.exam_id,
        student_id=row.student_id,
        student_id_raw=row.student_id_raw,
        answers={int(k): v for k, v in row.answers.items()},
        ambiguous=list(row.ambiguous),
        blank=list(row.blank),
        fills={int(k): v for k, v in row.fills.items()},
        confidence=row.confidence,
    )


# --- Conversión modelo <-> tablas ---

def _exam_row(session: Session, exam_id: str, owner_id: uuid.UUID | None) -> ExamRow | None:
    row = session.get(ExamRow, exam_id)
    if row is not None and owner_id is not None and row.owner_id != owner_id:
        logger.warning("examen '%s' es de otro usuario", exam_id)
        return None
    return row


def _fill_exam_row(row: ExamRow, exam: Exam) -> None:
    logger.debug("copiando examen '%s' a filas de la BD", exam.id)
    row.title = exam.name
    row.num_options = exam.num_options
    row.student_id_digits = exam.student_id_digits
    for position, section in enumerate(exam.sections):
        row.sections.append(SectionRow(
            key=section.id,
            name=section.name,
            weight=section.weight,
            position=position,
            questions=[
                QuestionRow(number=q.number, correct_answer=q.correct_answer, points=q.points)
                for q in section.questions
            ],
        ))


def _row_to_exam(row: ExamRow) -> Exam:
    logger.debug("convirtiendo fila de examen '%s' a Exam", row.id)
    return Exam(
        id=row.id,
        name=row.title,
        num_options=row.num_options,
        student_id_digits=row.student_id_digits,
        sections=[
            Section(
                id=s.key,
                name=s.name,
                weight=s.weight,
                questions=[
                    Question(number=q.number, correct_answer=q.correct_answer, points=q.points)
                    for q in s.questions
                ],
            )
            for s in row.sections
        ],
    )


def _fill_result_row(row: ResultRow, result: Result, reading: SheetReading) -> None:
    logger.debug("copiando resultado de matrícula %s a fila de la BD", result.student_id_raw)
    row.student_id = result.student_id
    row.student_id_raw = result.student_id_raw
    row.final_score = result.final_score
    row.max_score = result.max_score
    row.section_scores = [
        {"section_id": s.section_id, "name": s.name, "points": s.points,
         "max_points": s.max_points, "weight": s.weight, "percent": round(s.percent, 2)}
        for s in result.sections
    ]
    # JSON solo acepta llaves de texto
    row.answers = {str(k): v for k, v in result.answers.items()}
    row.ambiguous = list(result.ambiguous)
    row.blank = list(result.blank)
    row.fills = {str(k): v for k, v in reading.fills.items()}
    row.confidence = reading.confidence
    row.needs_review = result.needs_review


def _rescore_results(session: Session, exam: Exam) -> int:
    from core.scoring import score

    rows = list_results(session, exam.id)
    logger.info("recalificando %d resultados del examen '%s'", len(rows), exam.id)
    for row in rows:
        reading = row_to_reading(row)
        before = row.final_score
        _fill_result_row(row, score(exam, reading), reading)
        logger.debug("resultado %s (matrícula %s): %g -> %g",
                     row.id, row.student_id_raw, before, row.final_score)
    return len(rows)
