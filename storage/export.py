"""
Exportación de resultados a tabla (pandas). El CSV/Excel no es donde se
guardan los datos, solo una vista que se genera desde la BD.

Una fila por matrícula:
- Si la matrícula tiene un resultado elegido, sale con su nota.
- Si se escaneó varias veces y el profesor no ha elegido, sale con
  estado "elegir resultado" y sin nota.
- Hojas cuya matrícula no se pudo leer salen aparte, con estado "revisar
  matrícula".
"""

import logging

import pandas as pd
from sqlalchemy.orm import Session

from . import repository
from .tables import local_time

logger = logging.getLogger(__name__)


def results_dataframe(session: Session, exam_id: str) -> pd.DataFrame:
    logger.info("inicio: tabla de resultados del examen '%s'", exam_id)
    exam = repository.get_exam(session, exam_id)
    if exam is None:
        raise repository.ExamNotFoundError(f"No existe el examen '{exam_id}'")
    rows = repository.list_results(session, exam_id)
    pending = repository.pending_duplicates(session, exam_id)

    records = []
    for r in rows:
        if r.student_id in pending:
            continue
        if not r.selected and r.student_id is not None:
            continue  # duplicado ya resuelto a favor de otro
        record = {
            "matricula": r.student_id_raw,
            "estado": "revisar matrícula" if r.student_id is None
                      else "revisar preguntas" if r.ambiguous else "ok",
            "nota": r.final_score,
            "nota_maxima": r.max_score,
        }
        for s in r.section_scores:
            record[f"{s['name']} (%)"] = s["percent"]
        record["ambiguas"] = ",".join(map(str, r.ambiguous))
        record["en_blanco"] = ",".join(map(str, r.blank))
        record["escaneado"] = local_time(r.scanned_at).strftime("%Y-%m-%d %H:%M")
        record["id_resultado"] = str(r.id)
        records.append(record)

    for student_id, dupes in pending.items():
        records.append({
            "matricula": student_id,
            "estado": f"elegir resultado ({len(dupes)} escaneos)",
            "nota": None,
            "nota_maxima": dupes[0].max_score,
        })
        logger.debug("matrícula %s pendiente de elegir", student_id)

    df = pd.DataFrame.from_records(records)
    if not df.empty:
        df = df.sort_values("matricula", kind="stable").reset_index(drop=True)
    logger.info("fin: %d filas (%d pendientes de elegir)", len(df), len(pending))
    return df
