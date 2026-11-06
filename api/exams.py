"""
Rutas de "Mis exámenes": crear, ver, editar, borrar y bajar la hoja en PDF.

Cada profesor solo toca sus exámenes: todas las rutas reciben el usuario de
`api.auth.Auth` y un examen de otro usuario responde 404, igual que si no
existiera. Las reglas son las de siempre (`storage/repository.py`): un
examen con resultados no puede cambiar su estructura, y borrarlo borra sus
resultados.

    GET    /api/exams/{id}                   examen completo (+ resultados, si está bloqueado)
    POST   /api/exams                        crear (el id se genera solo)
    PUT    /api/exams/{id}                   editar (recalifica lo guardado)
    DELETE /api/exams/{id}                   borrar con sus resultados (y sus imágenes)
    GET    /api/exams/{id}/sheet.pdf         hoja para imprimir (?page=letter|a4)
    POST   /api/exams/preview                PNG de la hoja de un borrador, sin guardar
                                             (?page=, ?exam_id= al editar)
"""

import io
import logging
import re
import unicodedata
import uuid
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import sessionmaker

from api.auth import Auth
from core.layout import PAGE_SIZES_MM, LayoutError, build_layout
from core.sheet_pdf import render
from models.exam import Exam, ExamValidationError, _new_exam_id
from storage import repository
from storage.saving import delete_exam_saved

PREVIEW_DPI = 110  # vista previa: suficiente para verla en pantalla

logger = logging.getLogger(__name__)


class QuestionIn(BaseModel):
    number: int
    correct_answer: str = Field(max_length=1)
    points: float = 1.0


class SectionIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1)
    weight: float | None = None
    questions: list[QuestionIn]


class ExamIn(BaseModel):
    """Lo que manda el editor. Sin id: al crear se genera, al editar va en la ruta."""
    name: str = Field(min_length=1, max_length=200)
    num_options: int = 4
    student_id_digits: int = 6
    sections: list[SectionIn]

    def to_exam(self, exam_id: str) -> Exam:
        return Exam.from_dict({**self.model_dump(), "id": exam_id})


def register_exam_routes(app: FastAPI, session_factory: sessionmaker, auth: Auth,
                         images) -> None:

    def owned_exam(session, exam_id: str, owner_id: uuid.UUID) -> Exam:
        exam = repository.get_exam(session, exam_id, owner_id=owner_id)
        if exam is None:
            raise HTTPException(404, f"No existe el examen '{exam_id}'.")
        return exam

    @app.post("/api/exams/preview")
    def preview(body: ExamIn, page: str = "letter", exam_id: str = "vistaprevia",
                owner_id: uuid.UUID = Depends(auth)):
        """`exam_id`: al editar un examen, para que la vista previa muestre su id."""
        logger.info("inicio: vista previa de '%s' para %s (página %s)", body.name, owner_id, page)
        _check_page(page)
        exam = _checked(body.to_exam(exam_id[:64]), page)
        png = _preview_png(render(build_layout(exam, page=page)))
        logger.info("fin: vista previa (%d bytes)", len(png))
        return Response(png, media_type="image/png")

    @app.get("/api/exams/{exam_id}")
    def get_exam(exam_id: str, owner_id: uuid.UUID = Depends(auth)):
        with session_factory() as session:
            exam = owned_exam(session, exam_id, owner_id)
            n_results = repository.count_results(session, exam_id)
        logger.info("examen '%s' para %s (%d resultados)", exam_id, owner_id, n_results)
        return {**exam.to_dict(), "results": n_results, "locked": n_results > 0}

    @app.post("/api/exams", status_code=201)
    def create_exam(body: ExamIn, owner_id: uuid.UUID = Depends(auth)):
        logger.info("inicio: crear examen '%s' para %s", body.name, owner_id)
        exam = _checked(body.to_exam(_new_exam_id()), "letter")
        with session_factory() as session:
            try:
                repository.create_exam(session, exam, owner_id=owner_id)
            except repository.ExamExistsError:  # el id al azar ya existía: otro intento
                logger.warning("id '%s' repetido; se genera otro", exam.id)
                exam.id = _new_exam_id()
                repository.create_exam(session, exam, owner_id=owner_id)
        logger.info("fin: examen '%s' creado", exam.id)
        return {"id": exam.id}

    @app.put("/api/exams/{exam_id}")
    def update_exam(exam_id: str, body: ExamIn, owner_id: uuid.UUID = Depends(auth)):
        logger.info("inicio: editar examen '%s' de %s", exam_id, owner_id)
        exam = _checked(body.to_exam(exam_id), "letter")
        with session_factory() as session:
            owned_exam(session, exam_id, owner_id)
            try:
                rescored = repository.update_exam(session, exam, owner_id=owner_id)
            except repository.ExamLockedError as exc:
                raise HTTPException(409, str(exc)) from exc
        logger.info("fin: examen '%s' editado, %d resultados recalificados", exam_id, rescored)
        return {"id": exam_id, "rescored": rescored}

    @app.delete("/api/exams/{exam_id}")
    def delete_exam(exam_id: str, owner_id: uuid.UUID = Depends(auth)):
        logger.info("inicio: borrar examen '%s' de %s", exam_id, owner_id)
        with session_factory() as session:
            owned_exam(session, exam_id, owner_id)
            deleted = delete_exam_saved(session, exam_id, owner_id=owner_id, images=images)
        logger.info("fin: examen '%s' borrado con %d resultados", exam_id, deleted)
        return {"id": exam_id, "deleted_results": deleted}

    @app.get("/api/exams/{exam_id}/sheet.pdf")
    def sheet_pdf(exam_id: str, page: str = "letter", owner_id: uuid.UUID = Depends(auth)):
        logger.info("inicio: PDF del examen '%s' (página %s)", exam_id, page)
        _check_page(page)
        with session_factory() as session:
            exam = owned_exam(session, exam_id, owner_id)
        pdf = render(build_layout(exam, page=page))
        filename = f"{_slug(exam.name) or exam.id}_{page}.pdf"
        logger.info("fin: PDF de %d bytes (%s)", len(pdf), filename)
        return Response(pdf, media_type="application/pdf", headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


def _check_page(page: str) -> None:
    if page not in PAGE_SIZES_MM:
        raise HTTPException(422, f"Tamaño de página desconocido: {page!r}")


def _checked(exam: Exam, page: str) -> Exam:
    """Valida el examen y que quepa en una hoja; si no, 422 con los problemas."""
    try:
        build_layout(exam, page=page)  # también corre exam.validate()
    except ExamValidationError as exc:
        problems = [p.lstrip("- ") for p in str(exc).splitlines()[1:]]
        logger.info("examen inválido: %s", problems)
        raise HTTPException(422, {"message": "El examen tiene errores.",
                                  "problems": problems}) from exc
    except LayoutError as exc:
        logger.info("no cabe en la hoja: %s", exc)
        raise HTTPException(422, {"message": str(exc), "problems": []}) from exc
    logger.debug("examen '%s' válido y cabe en la hoja", exam.id)
    return exam


def _preview_png(pdf: bytes) -> bytes:
    import pypdfium2 as pdfium

    image = pdfium.PdfDocument(pdf)[0].render(scale=PREVIEW_DPI / 72).to_pil()
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    logger.debug("vista previa de %dx%d px", *image.size)
    return out.getvalue()


def _slug(text: str) -> str:
    """'Inglés II - Parcial 1' -> 'ingles_ii_parcial_1' (para el nombre del archivo)."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")[:60]
