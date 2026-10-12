"""
Calificación: SheetReading + Exam -> Result.

No usa imágenes, así que se prueba con casos calculados a mano.

Reglas (ver README, sección "Calificación"):
- Una pregunta suma sus `points` solo si la respuesta leída es la correcta.
  En blanco o ambigua vale 0; las ambiguas quedan marcadas para revisión.
- Nota de sección = puntos obtenidos / puntos posibles.
- Si las secciones tienen `weight`, la nota final es el promedio ponderado
  (0-100). Si no, es la suma simple de puntos.
"""

import logging

from models.exam import Exam
from models.result import Result, SectionScore, SheetReading

logger = logging.getLogger(__name__)


def score(exam: Exam, reading: SheetReading) -> Result:
    logger.info("inicio: calificando examen '%s', matrícula %s",
                exam.id, reading.student_id or reading.student_id_raw)

    extra = set(reading.answers) - {q.number for s in exam.sections for q in s.questions}
    if extra:
        logger.warning("la lectura trae preguntas que el examen no tiene: %s", sorted(extra))

    correct: list[int] = []
    sections: list[SectionScore] = []
    for section in exam.sections:
        points = 0.0
        for q in section.questions:
            answer = reading.answers.get(q.number)
            if answer == q.correct_answer:
                points += q.points
                correct.append(q.number)
            logger.debug("pregunta %d: leída %s, correcta %s -> %s",
                         q.number, answer, q.correct_answer,
                         "bien" if answer == q.correct_answer else "0 puntos")
        sec = SectionScore(section.id, section.name, points, section.max_points, section.weight)
        logger.info("sección '%s': %g de %g puntos (%.1f%%)",
                    section.id, points, sec.max_points, sec.percent)
        sections.append(sec)

    if all(s.weight is not None for s in sections):
        final = sum(s.percent * s.weight / 100 for s in sections)
        max_score = 100.0
        logger.debug("con pesos: promedio ponderado de %s",
                     [(s.section_id, round(s.percent, 2), s.weight) for s in sections])
    else:
        final = sum(s.points for s in sections)
        max_score = sum(s.max_points for s in sections)
        logger.debug("sin pesos: suma simple de puntos")

    result = Result(
        exam_id=exam.id,
        student_id=reading.student_id,
        student_id_raw=reading.student_id_raw,
        answers=dict(reading.answers),
        correct=correct,
        ambiguous=list(reading.ambiguous),
        blank=list(reading.blank),
        sections=sections,
        final_score=round(final, 2),
        max_score=max_score,
    )
    logger.info("fin: nota %g de %g (%d correctas, %d en blanco, %d ambiguas)%s",
                result.final_score, max_score, len(correct), len(result.blank),
                len(result.ambiguous), " — REVISAR" if result.needs_review else "")
    return result
