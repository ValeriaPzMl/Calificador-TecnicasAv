"""
Estructuras de datos de un examen. Deliberadamente no saben nada de OpenCV
ni de imágenes: solo modelan "qué preguntas hay, cuánto vale cada una, y
cuál es la respuesta correcta". Esto es lo que le da soporte a la parte
"profesional" del proyecto (secciones, ponderaciones) sin acoplarla al
motor de visión.

`core/layout.py` usa un Exam para decidir dónde va cada burbuja en la hoja.
`core/detection.py` produce un `SheetReading` (respuesta cruda por
pregunta, ej. {1: "B", 2: "D"}) y `core/scoring.py` lo combina con un Exam
para producir un `Result` (ver `models/result.py`).

Semántica de los valores:
- `Question.points`: lo que vale la pregunta dentro de su sección.
- `Section.weight`: % de la nota final (0-100). Si alguna sección tiene
  weight, todas deben tenerlo y deben sumar 100. Si ninguna lo tiene, la
  nota final es la suma simple de puntos.
"""

import json
import logging
import string
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAX_OPTIONS = 8  # A-H; más opciones no caben bien en una fila de la hoja

logger = logging.getLogger(__name__)


class ExamValidationError(ValueError):
    """El examen tiene datos inconsistentes (respuestas inválidas, pesos
    que no suman 100, preguntas repetidas, etc)."""


@dataclass
class Question:
    number: int
    correct_answer: str  # ej. "A", "B", "C", "D"
    points: float = 1.0


@dataclass
class Section:
    id: str
    name: str
    questions: list[Question] = field(default_factory=list)
    weight: float | None = None  # % de la nota final; None = suma simple de puntos

    @property
    def max_points(self) -> float:
        return sum(q.points for q in self.questions)


def _new_exam_id() -> str:
    exam_id = uuid.uuid4().hex[:12]
    logger.debug("el examen no traía id, se generó %s", exam_id)
    return exam_id


@dataclass
class Exam:
    name: str
    sections: list[Section] = field(default_factory=list)
    id: str = field(default_factory=_new_exam_id)
    num_options: int = 4  # A, B, C, D
    student_id_digits: int = 6  # dígitos de la matrícula

    @property
    def total_questions(self) -> int:
        return sum(len(s.questions) for s in self.sections)

    @property
    def options(self) -> list[str]:
        """Letras de opción válidas para este examen, ej. ["A", "B", "C", "D"]."""
        return list(string.ascii_uppercase[: self.num_options])

    def question_by_number(self, number: int) -> Question | None:
        for section in self.sections:
            for question in section.questions:
                if question.number == number:
                    logger.debug("pregunta %d encontrada en sección '%s'", number, section.id)
                    return question
        logger.debug("pregunta %d no existe en el examen", number)
        return None

    def validate(self) -> None:
        """Lanza ExamValidationError con todos los problemas encontrados,
        en vez de dejar que el layout o la calificación fallen después con
        un error confuso."""
        logger.info(
            "inicio: examen '%s' (%d secciones, %d preguntas, %d opciones, matrícula de %d dígitos)",
            self.id, len(self.sections), self.total_questions, self.num_options,
            self.student_id_digits,
        )
        problems = []

        if not 2 <= self.num_options <= MAX_OPTIONS:
            problems.append(f"num_options debe estar entre 2 y {MAX_OPTIONS}")
        if not 1 <= self.student_id_digits <= 12:
            problems.append("student_id_digits debe estar entre 1 y 12")
        if not self.sections:
            problems.append("el examen no tiene secciones")

        seen_numbers: set[int] = set()
        for section in self.sections:
            logger.debug(
                "revisando sección '%s': %d preguntas, %g puntos, weight=%s",
                section.id, len(section.questions), section.max_points, section.weight,
            )
            if not section.questions:
                problems.append(f"la sección '{section.name}' no tiene preguntas")
            for q in section.questions:
                if q.number in seen_numbers:
                    problems.append(f"la pregunta {q.number} está repetida")
                seen_numbers.add(q.number)
                if q.correct_answer not in self.options:
                    problems.append(
                        f"pregunta {q.number}: respuesta '{q.correct_answer}' "
                        f"no está en {self.options}"
                    )
                if q.points <= 0:
                    problems.append(f"pregunta {q.number}: points debe ser > 0")

        weights = [s.weight for s in self.sections]
        if any(w is not None for w in weights):
            if any(w is None for w in weights):
                problems.append("si una sección tiene weight, todas deben tenerlo")
            elif abs(sum(weights) - 100) > 1e-6:
                problems.append(f"los weight de las secciones suman {sum(weights)}, no 100")

        if problems:
            logger.warning("examen '%s' inválido, %d problema(s): %s", self.id, len(problems), problems)
            raise ExamValidationError("Examen inválido:\n- " + "\n- ".join(problems))
        logger.info("fin: examen '%s' válido", self.id)

    def structure(self) -> tuple:
        """Lo que decide dónde va cada burbuja en la hoja impresa.

        Si esto cambia, las hojas ya impresas dejan de coincidir con el
        layout. Por eso no se puede cambiar en un examen que ya tiene
        resultados. Respuestas correctas, puntos, pesos y nombres sí se
        pueden cambiar: no mueven ninguna burbuja.
        """
        structure = (
            self.num_options,
            self.student_id_digits,
            tuple(
                (section.id, tuple(q.number for q in section.questions))
                for section in self.sections
            ),
        )
        logger.debug("estructura del examen '%s': %s", self.id, structure)
        return structure

    # --- Serialización JSON (archivos de ejemplo e importación a la BD) ---

    def to_dict(self) -> dict:
        logger.debug("serializando examen '%s' a dict", self.id)
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Exam":
        logger.info("inicio: construyendo Exam desde dict (id=%s, %d secciones)",
                    data.get("id"), len(data.get("sections", [])))
        sections = [
            Section(
                id=s["id"],
                name=s["name"],
                weight=s.get("weight"),
                questions=[Question(**q) for q in s.get("questions", [])],
            )
            for s in data.get("sections", [])
        ]
        kwargs = {
            k: data[k]
            for k in ("id", "num_options", "student_id_digits")
            if k in data
        }
        for s in sections:
            logger.debug("sección '%s' (%s): %d preguntas, weight=%s",
                         s.id, s.name, len(s.questions), s.weight)
        missing = {"id", "num_options", "student_id_digits"} - set(kwargs)
        if missing:
            logger.debug("campos no incluidos, se usan los valores por defecto: %s", sorted(missing))
        exam = cls(name=data["name"], sections=sections, **kwargs)
        logger.info("fin: Exam '%s' (%s) con %d preguntas", exam.id, exam.name, exam.total_questions)
        return exam


def load_exam(path: str | Path) -> Exam:
    """Lee y valida un examen desde un archivo JSON."""
    logger.info("inicio: leyendo %s", path)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    logger.debug("JSON leído, llaves: %s", sorted(data))
    exam = Exam.from_dict(data)
    exam.validate()
    logger.info("fin: examen '%s' cargado y validado", exam.id)
    return exam


def save_exam(exam: Exam, path: str | Path) -> None:
    logger.info("guardando examen '%s' en %s", exam.id, path)
    Path(path).write_text(
        json.dumps(exam.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
