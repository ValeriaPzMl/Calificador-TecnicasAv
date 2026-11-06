"""
Rutas de "Resultados": ver las hojas calificadas de un examen, corregirlas a
mano, elegir cuál cuenta cuando una matrícula se escaneó varias veces,
borrar hojas y exportar a CSV o Excel.

Como en `api/exams.py`, cada profesor solo toca lo suyo: un examen o una
hoja de otro usuario responde 404.

    GET    /api/exams/{id}/results           hojas del examen
    GET    /api/exams/{id}/results.csv       exportar (una fila por matrícula)
    GET    /api/exams/{id}/results.xlsx      lo mismo en Excel
    GET    /api/results/{rid}                una hoja
    GET    /api/results/{rid}/review.jpg     su imagen de revisión
    PATCH  /api/results/{rid}                corregir respuestas y/o matrícula
    POST   /api/results/{rid}/choose         esta hoja es la que cuenta
    DELETE /api/results/{rid}                borrar la hoja
"""

import io
import logging
import uuid
from urllib.parse import quote

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from api.auth import Auth
from api.exams import _slug
from storage import repository
from storage.export import results_dataframe
from storage.images import ImageStoreError
from storage.saving import delete_saved, review_jpeg
from storage.tables import ResultRow

REVIEW_MAX_SIDE = 1600
CSV_TYPE = "text/csv; charset=utf-8"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

logger = logging.getLogger(__name__)


class CorrectionIn(BaseModel):
    """Solo lo que cambió. `answers`: {número: letra o null (en blanco)}."""
    answers: dict[int, str | None] = {}
    student_id: str | None = None


def result_dict(row: ResultRow) -> dict:
    return {
        "id": str(row.id),
        "exam_id": row.exam_id,
        "student_id": row.student_id,
        "student_id_raw": row.student_id_raw,
        "final_score": row.final_score,
        "max_score": row.max_score,
        "sections": row.section_scores,
        "answers": row.answers,
        "ambiguous": row.ambiguous,
        "blank": row.blank,
        "needs_review": row.needs_review,
        "selected": row.selected,
        "confidence": row.confidence,
        "scanned_at": row.scanned_at.isoformat(),
        "has_image": bool(row.image_path),
    }


def register_result_routes(app: FastAPI, session_factory: sessionmaker, auth: Auth,
                           images) -> None:

    def owned_exam(session: Session, exam_id: str, owner_id: uuid.UUID):
        exam = repository.get_exam(session, exam_id, owner_id=owner_id)
        if exam is None:
            raise HTTPException(404, f"No existe el examen '{exam_id}'.")
        return exam

    def owned_result(session: Session, result_id: str, owner_id: uuid.UUID) -> ResultRow:
        try:
            row = repository.get_result(session, uuid.UUID(result_id))
        except ValueError:
            row = None
        if row is None or row.owner_id != owner_id:
            if row is not None:
                logger.warning("resultado %s es de otro usuario", result_id)
            raise HTTPException(404, "No existe esa hoja.")
        return row

    @app.get("/api/exams/{exam_id}/results")
    def list_results(exam_id: str, owner_id: uuid.UUID = Depends(auth)):
        with session_factory() as session:
            exam = owned_exam(session, exam_id, owner_id)
            rows = repository.list_results(session, exam_id)
            payload = [result_dict(r) for r in rows]
        logger.info("%d hojas del examen '%s' para %s", len(payload), exam_id, owner_id)
        return {"exam": {"id": exam.id, "name": exam.name}, "results": payload}

    @app.get("/api/exams/{exam_id}/results.{fmt}")
    def export_results(exam_id: str, fmt: str, owner_id: uuid.UUID = Depends(auth)):
        logger.info("inicio: exportar resultados de '%s' a %s", exam_id, fmt)
        if fmt not in ("csv", "xlsx"):
            raise HTTPException(404, "Formato desconocido (csv o xlsx).")
        with session_factory() as session:
            exam = owned_exam(session, exam_id, owner_id)
            df = results_dataframe(session, exam_id)
        if fmt == "csv":
            data = df.to_csv(index=False).encode("utf-8-sig")  # -sig: Excel muestra los acentos
            media_type = CSV_TYPE
        else:
            data = _xlsx(df)
            media_type = XLSX_TYPE
        filename = f"{_slug(exam.name) or exam.id}_resultados.{fmt}"
        logger.info("fin: %d filas, %d bytes (%s)", len(df), len(data), filename)
        return Response(data, media_type=media_type, headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})

    @app.get("/api/results/{result_id}")
    def get_result(result_id: str, owner_id: uuid.UUID = Depends(auth)):
        with session_factory() as session:
            row = owned_result(session, result_id, owner_id)
            logger.info("hoja %s (matrícula %s)", result_id, row.student_id_raw)
            return result_dict(row)

    @app.get("/api/results/{result_id}/review.jpg")
    def review_image(result_id: str, owner_id: uuid.UUID = Depends(auth)):
        with session_factory() as session:
            image_path = owned_result(session, result_id, owner_id).image_path
        try:
            data = images.get(image_path) if image_path else None
        except ImageStoreError as exc:
            raise HTTPException(503, "No se pudo bajar la imagen; intenta de nuevo.") from exc
        jpg = review_jpeg(data, REVIEW_MAX_SIDE)
        if jpg is None:
            raise HTTPException(404, "Esta hoja no tiene imagen de revisión.")
        logger.info("imagen de revisión de %s (%d bytes)", result_id, len(jpg))
        return Response(jpg, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600"})

    @app.patch("/api/results/{result_id}")
    def correct_result(result_id: str, body: CorrectionIn, owner_id: uuid.UUID = Depends(auth)):
        logger.info("inicio: corregir hoja %s de %s", result_id, owner_id)
        student_id = (body.student_id or "").strip() or None
        answers = {n: (a.strip().upper() or None) if a else None for n, a in body.answers.items()}
        with session_factory() as session:
            row = owned_result(session, result_id, owner_id)
            exam = owned_exam(session, row.exam_id, owner_id)
            try:
                repository.correct_result(session, row, exam, answers, student_id)
            except repository.ResultValidationError as exc:
                raise HTTPException(422, str(exc)) from exc
            others = repository.results_of_student(session, row.exam_id, row.student_id,
                                                   exclude=row.id) if row.student_id else []
            payload = {**result_dict(row), "duplicates": len(others)}
        logger.info("fin: hoja %s corregida (nota %g)", result_id, payload["final_score"])
        return payload

    @app.post("/api/results/{result_id}/choose")
    def choose_result(result_id: str, owner_id: uuid.UUID = Depends(auth)):
        with session_factory() as session:
            row = owned_result(session, result_id, owner_id)
            repository.choose_result(session, row.id)
        logger.info("hoja %s elegida por %s", result_id, owner_id)
        return {"id": result_id, "selected": True}

    @app.delete("/api/results/{result_id}")
    def delete_result(result_id: str, owner_id: uuid.UUID = Depends(auth)):
        logger.info("inicio: borrar hoja %s de %s", result_id, owner_id)
        with session_factory() as session:
            delete_saved(session, owned_result(session, result_id, owner_id), images)
        logger.info("fin: hoja %s borrada", result_id)
        return {"id": result_id, "deleted": True}


def _xlsx(df) -> bytes:
    """El DataFrame como Excel, con columnas del ancho de su contenido."""
    from openpyxl.utils import get_column_letter

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Resultados")
        sheet = writer.sheets["Resultados"]
        for i, column in enumerate(df.columns, start=1):
            width = max([len(str(column))] + [len(str(v)) for v in df[column].tolist()])
            sheet.column_dimensions[get_column_letter(i)].width = min(width + 2, 50)
        sheet.freeze_panes = "A2"
    logger.debug("Excel con %d filas y %d columnas", len(df), len(df.columns))
    return out.getvalue()
