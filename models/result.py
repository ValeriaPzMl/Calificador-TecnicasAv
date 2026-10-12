"""
Lo que sale de leer una hoja (`SheetReading`) y de calificarla (`Result`).

Igual que `exam.py`, no sabe nada de OpenCV ni de la base de datos:
`core/detection.py` llena un SheetReading, `core/scoring.py` lo convierte
en Result y `storage/` lo guarda.

Estados de una pregunta en la lectura:
- contestada: `answers[n]` es la letra.
- en blanco: `answers[n]` es None y `n` está en `blank`. Vale 0.
- ambigua: `answers[n]` es None y `n` está en `ambiguous` (dos marcas, una
  marca tenue o a medias...). Vale 0 y queda para que el profesor la
  revise. **Nunca se adivina.**
"""

from dataclasses import asdict, dataclass, field


@dataclass
class SheetReading:
    exam_id: str | None
    student_id: str | None  # None si algún dígito no se pudo leer
    student_id_raw: str  # lo que se leyó, con "?" en los dígitos dudosos, ej. "20?517"
    answers: dict[int, str | None]
    ambiguous: list[int] = field(default_factory=list)
    blank: list[int] = field(default_factory=list)
    # Oscuridad (0-1) de cada burbuja, por pregunta. Sirve para revisar
    # por qué una pregunta quedó ambigua.
    fills: dict[int, list[float]] = field(default_factory=dict)
    # Qué tan clara fue la lectura: la menor diferencia entre la burbuja
    # elegida y la segunda más oscura (1 = perfecta, 0 = no se distingue).
    confidence: float = 1.0


@dataclass
class SectionScore:
    section_id: str
    name: str
    points: float
    max_points: float
    weight: float | None

    @property
    def percent(self) -> float:
        return 100 * self.points / self.max_points if self.max_points else 0.0


@dataclass
class Result:
    exam_id: str
    student_id: str | None
    student_id_raw: str
    answers: dict[int, str | None]
    correct: list[int]
    ambiguous: list[int]
    blank: list[int]
    sections: list[SectionScore]
    # Con pesos: 0-100 (promedio ponderado). Sin pesos: suma de puntos.
    final_score: float
    max_score: float

    @property
    def needs_review(self) -> bool:
        """El profesor debe revisar: hay preguntas ambiguas o la matrícula
        no se leyó completa."""
        return bool(self.ambiguous) or self.student_id is None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["needs_review"] = self.needs_review
        return data
