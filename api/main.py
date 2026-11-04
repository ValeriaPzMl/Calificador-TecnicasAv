"""
API para calificar desde el navegador (celular o computadora).

Cada profesor inicia sesión (Supabase) y solo ve y califica con sus propios
exámenes: todas las rutas reciben el usuario de `api.auth.Auth` y lo pasan
como `owner_id` al repositorio. Sin Supabase configurado (modo local) no se
pide sesión y todo es del usuario por defecto, como en el CLI. Sirve
también la página de prueba (`api/static/index.html`), que ofrece dos
formas de calificar:

- **Cámara en vivo:** el navegador abre la cámara del dispositivo y manda
  cuadros uno por uno. La API mantiene un `LiveScanner` por sesión de
  escaneo; al llegar a "lista" guarda el resultado y lo regresa.
- **Foto:** se toma con la cámara nativa del celular (mejor calidad) o se
  sube de la galería, y se califica con `grade_photo`, igual que `cli.grade`.

Rutas:
    GET    /                                 página de prueba
    GET    /api/config                       si hay que iniciar sesión y con qué proyecto
    GET    /api/me                           el usuario de la sesión
    GET    /api/exams                        exámenes del usuario
    POST   /api/scan/sessions                empezar un escaneo en vivo
    POST   /api/scan/sessions/{id}/frame     mandar un cuadro (multipart "frame")
    DELETE /api/scan/sessions/{id}           cancelar el escaneo
    POST   /api/grade                        calificar una foto (multipart "photo")

Se levanta con `python -m api.serve` (HTTPS, para que el celular deje usar
la cámara).
"""

import base64
import logging
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import sessionmaker

from api.auth import Auth
from core.alignment import AlignmentError
from core.layout import PAGE_SIZES_MM
from core.live import READY, FrameStatus, LiveScanner
from core.pipeline import GradedSheet, GradingError, grade_photo
from storage import repository
from storage.saving import save_graded

STATIC_DIR = Path(__file__).resolve().parent / "static"
SESSION_TTL_S = 15 * 60  # un escaneo sin cuadros en este tiempo se descarta
CORNER_ORDER = ["top_left", "top_right", "bottom_right", "bottom_left"]
REVIEW_MAX_SIDE = 1600  # tamaño de la imagen de revisión que se manda al navegador

logger = logging.getLogger(__name__)


class ScanOptions(BaseModel):
    exam_id: str | None = None  # examen de respaldo, por si el QR no se lee
    page: str = "letter"
    dry_run: bool = False


class _ScanSession:
    def __init__(self, scanner: LiveScanner, options: ScanOptions, owner_id: uuid.UUID):
        self.scanner = scanner
        self.options = options
        self.owner_id = owner_id
        self.lock = threading.Lock()  # un cuadro a la vez por escaneo
        self.last_used = time.monotonic()
        self.result: dict | None = None  # el resultado, una vez guardado


def create_app(session_factory: sessionmaker, auth: Auth | None = None) -> FastAPI:
    """Crea la app. `session_factory` abre sesiones de la BD (una por
    petición) y `auth` dice quién hace cada petición (sin él, modo local
    sin sesión); así las pruebas pueden pasar una BD en memoria y llaves
    de prueba."""
    auth = auth or Auth()
    app = FastAPI(title="Calificador")
    scans: dict[str, _ScanSession] = {}
    scans_lock = threading.Lock()

    def exam_finder(owner_id: uuid.UUID):
        """`find_exam` para el pipeline: solo encuentra exámenes del usuario."""
        def find_exam(exam_id: str):
            with session_factory() as session:
                return repository.get_exam(session, exam_id, owner_id=owner_id)
        return find_exam

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/config")
    def config():
        return auth.public_config()

    @app.get("/api/me")
    def me(owner_id: uuid.UUID = Depends(auth)):
        return {"user_id": str(owner_id), "auth": auth.enabled}

    @app.get("/api/exams")
    def list_exams(owner_id: uuid.UUID = Depends(auth)):
        with session_factory() as session:
            exams = repository.list_exams(session, owner_id=owner_id)
        logger.info("%d exámenes", len(exams))
        return [{"id": e.id, "title": e.title, "questions": e.questions, "results": e.results}
                for e in exams]

    @app.post("/api/scan/sessions")
    def start_scan(options: ScanOptions, owner_id: uuid.UUID = Depends(auth)):
        _check_page(options.page)
        with scans_lock:
            _drop_stale(scans)
            scan_id = uuid.uuid4().hex
            scans[scan_id] = _ScanSession(
                LiveScanner(exam_finder(owner_id), fallback_exam_id=options.exam_id or None,
                            fallback_page=options.page),
                options,
                owner_id,
            )
        logger.info("escaneo %s iniciado por %s (%s); %d activos", scan_id, owner_id, options,
                    len(scans))
        return {"scan_id": scan_id}

    def get_scan(scan_id: str, owner_id: uuid.UUID) -> _ScanSession:
        scan = scans.get(scan_id)
        if scan is None or scan.owner_id != owner_id:
            if scan is not None:
                logger.warning("escaneo %s es de otro usuario", scan_id)
            raise HTTPException(404, "El escaneo no existe o ya expiró; empieza otro.")
        return scan

    @app.post("/api/scan/sessions/{scan_id}/frame")
    def scan_frame(scan_id: str, frame: UploadFile = File(...),
                   owner_id: uuid.UUID = Depends(auth)):
        scan = get_scan(scan_id, owner_id)
        image = _decode(frame.file.read())
        with scan.lock:
            scan.last_used = time.monotonic()
            status = scan.scanner.process(image)
            payload = _status_payload(status, image)
            if status.state == READY:
                if scan.result is None:  # se guarda una sola vez
                    scan.result = _save(session_factory, status.graded, "vivo",
                                        scan.options.dry_run, owner_id,
                                        capture=status.capture)
                payload["result"] = scan.result
        return payload

    @app.delete("/api/scan/sessions/{scan_id}")
    def stop_scan(scan_id: str, owner_id: uuid.UUID = Depends(auth)):
        with scans_lock:
            scan = get_scan(scan_id, owner_id)
            del scans[scan_id]
        logger.info("escaneo %s terminado (%s)", scan_id,
                    "con resultado" if scan.result else "sin resultado")
        return {"ok": True}

    @app.post("/api/grade")
    def grade(photo: UploadFile = File(...), exam_id: str = Form(""), page: str = Form("letter"),
              dry_run: bool = Form(False), owner_id: uuid.UUID = Depends(auth)):
        logger.info("inicio: foto subida %s por %s (exam_id=%r, dry_run=%s)",
                    photo.filename, owner_id, exam_id, dry_run)
        _check_page(page)
        image = _decode(photo.file.read())
        try:
            graded = grade_photo(image, find_exam=exam_finder(owner_id),
                                 fallback_exam_id=exam_id or None, fallback_page=page)
        except (AlignmentError, GradingError) as exc:
            logger.warning("no se pudo calificar la foto: %s", exc)
            raise HTTPException(422, str(exc)) from exc
        result = _save(session_factory, graded, Path(photo.filename or "foto").stem, dry_run,
                       owner_id)
        logger.info("fin: foto calificada")
        return {"result": result}

    return app


def _check_page(page: str) -> None:
    if page not in PAGE_SIZES_MM:
        raise HTTPException(422, f"Tamaño de página desconocido: {page!r}")


def _decode(data: bytes) -> np.ndarray:
    """Bytes de JPEG/PNG -> imagen BGR."""
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        logger.warning("no se pudo leer la imagen recibida (%d bytes)", len(data))
        raise HTTPException(400, "No se pudo leer la imagen (¿es JPG o PNG?).")
    logger.debug("imagen recibida de %dx%d px (%d bytes)", image.shape[1], image.shape[0],
                 len(data))
    return image


def _drop_stale(scans: dict[str, _ScanSession]) -> None:
    now = time.monotonic()
    for scan_id in [k for k, s in scans.items() if now - s.last_used > SESSION_TTL_S]:
        logger.info("escaneo %s expirado", scan_id)
        del scans[scan_id]


def _status_payload(status: FrameStatus, image: np.ndarray) -> dict:
    """Lo que necesita la página para dibujar: estado, mensaje, esquinas (en
    px del cuadro que mandó) y progreso."""
    return {
        "state": status.state,
        "message": status.message,
        "corners": {k: [round(x, 1), round(y, 1)] for k, (x, y) in status.corners.items()},
        "corner_order": CORNER_ORDER,
        "frame_size": [image.shape[1], image.shape[0]],
        "progress": status.progress,
        "needed": status.needed,
        "exam_id": status.exam_id,
    }


def _save(session_factory: sessionmaker, graded: GradedSheet, stem: str, dry_run: bool,
          owner_id: uuid.UUID, capture: np.ndarray | None = None) -> dict:
    """Guarda la hoja (BD + imagen de revisión, como el CLI) a nombre del
    usuario y arma la respuesta con la nota."""
    stem = f"{stem}_{datetime.now():%Y%m%d_%H%M%S}"
    with session_factory() as session:
        result_id, image_path, others = save_graded(session, graded, stem, dry_run,
                                                    owner_id=owner_id)
    if capture is not None:
        name = f"{stem}_captura.jpg" if dry_run else f"{result_id}_captura.jpg"
        cv2.imwrite(str(Path(image_path).with_name(name)), capture)
    return _result_payload(graded, result_id, image_path, len(others), saved=not dry_run)


def _result_payload(graded: GradedSheet, result_id: str, image_path: Path, duplicates: int,
                    saved: bool) -> dict:
    result, reading = graded.result, graded.reading
    return {
        "result_id": result_id if saved else None,
        "saved": saved,
        "exam": {"id": graded.exam.id, "name": graded.exam.name},
        "qr_read": graded.qr_read,
        "student_id": result.student_id,
        "student_id_raw": reading.student_id_raw,
        "final_score": result.final_score,
        "max_score": result.max_score,
        "sections": [{"name": s.name, "points": s.points, "max_points": s.max_points,
                      "percent": round(s.percent, 1), "weight": s.weight}
                     for s in result.sections],
        "ambiguous": result.ambiguous,
        "blank": result.blank,
        "needs_review": result.needs_review,
        "duplicates": duplicates,
        "review_image": _review_data_url(image_path),
    }


def _review_data_url(image_path: Path) -> str | None:
    """La imagen de revisión como data URL (JPEG reducido), para mostrarla
    sin tener que servir archivos del disco."""
    image = cv2.imread(str(image_path))
    if image is None:
        logger.warning("no se pudo leer la imagen de revisión %s", image_path)
        return None
    h, w = image.shape[:2]
    scale = REVIEW_MAX_SIDE / max(h, w)
    if scale < 1:
        image = cv2.resize(image, (round(w * scale), round(h * scale)),
                           interpolation=cv2.INTER_AREA)
    ok, jpg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode() if ok else None
