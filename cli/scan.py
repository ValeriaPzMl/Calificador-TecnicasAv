"""
Califica con la cámara en vivo, sin tomar fotos.

Uso:
    python -m cli.scan                  # webcam de la computadora
    python -m cli.scan --camera 1       # otra cámara
    python -m cli.scan --camera http://192.168.1.20:8080/video   # celular (app tipo IP Webcam)
    python -m cli.scan --dry-run -v     # sin guardar, viendo el flujo

Es el mismo flujo que tendrá la web:
1. Se abre la cámara. Muestra la hoja con su contorno (verde = se ven las 4
   esquinas) y un mensaje de qué está pasando.
2. Cuando la lectura sale igual en varios cuadros seguidos, **cierra la
   cámara sola**, guarda el resultado en la BD y muestra la nota.
3. Opciones: [N] calificar otro (vuelve a abrir la cámara) · [Q] salir.

Durante el escaneo, Q o Esc sale. Igual que con `cli.grade`, por cada hoja
se guarda la imagen de revisión y también el cuadro capturado
(data/processed/results/<examen>/).
"""

import argparse
import logging
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from cli.grade import print_result
from cli.logs import add_verbose_flag, setup_logging
from core.layout import PAGE_SIZES_MM
from core.live import (
    BLURRY, NO_QR, READING, READY, SEARCHING, UNKNOWN_EXAM, FrameStatus, LiveScanner,
)
from storage import repository
from storage.db import open_session
from storage.saving import save_graded

WINDOW = "Calificador"
DISPLAY_W = 1280  # ancho máximo de la ventana
REQUEST_SIZE = (1920, 1080)  # resolución que se le pide a la cámara
FONT_PATHS = {
    False: "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Regular.ttf",
    True: "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Bold.ttf",
}
STATE_COLORS = {  # BGR
    SEARCHING: (60, 60, 220),
    UNKNOWN_EXAM: (60, 60, 220),
    NO_QR: (0, 190, 255),
    BLURRY: (0, 190, 255),
    READING: (60, 180, 60),
    READY: (60, 180, 60),
}
CORNER_ORDER = ["top_left", "top_right", "bottom_right", "bottom_left"]

logger = logging.getLogger(__name__)


class Quit(Exception):
    """El usuario pidió salir."""


def open_camera(source: str) -> cv2.VideoCapture:
    src: int | str = int(source) if source.isdigit() else source
    logger.info("abriendo cámara %r", src)
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise SystemExit(f"ERROR: no se pudo abrir la cámara {source!r}")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, REQUEST_SIZE[0])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, REQUEST_SIZE[1])
    w, h = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    logger.info("cámara abierta a %dx%d", w, h)
    return cap


def scan_one(cap_source: str, scanner: LiveScanner) -> FrameStatus:
    """Abre la cámara y procesa cuadros hasta tener una lectura lista.
    Cierra la cámara al terminar. Lanza Quit si el usuario sale."""
    cap = open_camera(cap_source)
    failures = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                failures += 1
                if failures > 30:
                    raise SystemExit("ERROR: la cámara dejó de mandar imagen")
                continue
            failures = 0
            status = scanner.process(frame)
            show(draw_live(frame, status))
            if status.state == READY:
                return status
            if wait_key(1) in (ord("q"), 27):
                raise Quit
    finally:
        cap.release()
        logger.info("cámara cerrada")


def wait_key(delay: int) -> int:
    key = cv2.waitKey(delay) & 0xFF
    if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
        raise Quit  # cerraron la ventana con la X
    return key


def show(image: np.ndarray) -> None:
    h, w = image.shape[:2]
    if w > DISPLAY_W:
        image = cv2.resize(image, (DISPLAY_W, round(h * DISPLAY_W / w)))
    cv2.imshow(WINDOW, image)


def put_text(image: np.ndarray, lines: list[tuple[str, int, bool, tuple]], x: int, y: int,
             spacing: float = 1.35) -> None:
    """Escribe texto con acentos (OpenCV no los soporta): (texto, tamaño,
    negrita, color BGR) por línea, empezando en (x, y)."""
    pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil)
    for text, size, bold, (b, g, r) in lines:
        draw.text((x, y), text, font=ImageFont.truetype(FONT_PATHS[bold], size), fill=(r, g, b))
        y += round(size * spacing)
    image[:] = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)


def draw_live(frame: np.ndarray, status: FrameStatus) -> np.ndarray:
    """Cuadro de la cámara con el contorno de la hoja, el mensaje y el progreso."""
    out = frame.copy()
    color = STATE_COLORS[status.state]
    thick = max(2, frame.shape[1] // 400)
    if len(status.corners) == 4:
        pts = np.array([status.corners[k] for k in CORNER_ORDER], np.int32)
        cv2.polylines(out, [pts], True, color, thick * 2)
    for x, y in status.corners.values():
        cv2.circle(out, (round(x), round(y)), thick * 6, color, thick)

    scale = frame.shape[1] / DISPLAY_W  # el texto se ve igual sin importar la resolución
    bar_h = round(70 * scale)
    overlay = out.copy()
    cv2.rectangle(overlay, (0, 0), (out.shape[1], bar_h), (30, 30, 30), -1)
    cv2.addWeighted(overlay, 0.65, out, 0.35, 0, out)
    exam = f"Examen: {status.exam_id}" if status.exam_id else "Examen: —"
    put_text(out, [(status.message, round(26 * scale), True, color),
                   (f"{exam}     ·     Q / Esc: salir", round(16 * scale), False, (220, 220, 220))],
             round(16 * scale), round(10 * scale))

    # Progreso: un segmento por cuadro estable que se necesita
    seg_w, gap = round(40 * scale), round(6 * scale)
    x0 = out.shape[1] - status.needed * (seg_w + gap) - round(16 * scale)
    for i in range(status.needed):
        filled = i < status.progress
        x = x0 + i * (seg_w + gap)
        cv2.rectangle(out, (x, round(24 * scale)), (x + seg_w, round(44 * scale)),
                      (60, 180, 60) if filled else (110, 110, 110), -1)
    return out


def draw_result(review: np.ndarray, status: FrameStatus, others: list, saved: bool) -> np.ndarray:
    """Pantalla final: la imagen de revisión y la nota, con las opciones."""
    graded = status.graded
    result, reading = graded.result, graded.reading
    target_h = 860
    review = cv2.resize(review, (round(review.shape[1] * target_h / review.shape[0]), target_h))
    panel_w = 520
    out = np.full((target_h, review.shape[1] + panel_w, 3), 250, np.uint8)
    out[:, :review.shape[1]] = review

    dark, muted = (30, 30, 30), (110, 110, 110)
    red, orange, green = (40, 40, 200), (0, 130, 230), (40, 140, 40)
    lines = [
        (graded.exam.name, 24, True, dark),
        (f"Examen {graded.exam.id}", 16, False, muted),
        ("", 10, False, dark),
        ("Matrícula", 16, False, muted),
        (reading.student_id_raw + ("   ← incompleta" if not result.student_id else ""), 34, True,
         red if not result.student_id else dark),
        ("Nota", 16, False, muted),
        (f"{result.final_score:g} / {result.max_score:g}", 48, True, green),
        ("", 6, False, dark),
    ]
    for s in result.sections:
        weight = f" · vale {s.weight:g}%" if s.weight is not None else ""
        lines.append((f"{s.name}: {s.points:g}/{s.max_points:g} ({s.percent:.0f}%){weight}",
                      18, False, dark))
    lines.append(("", 6, False, dark))
    if result.ambiguous:
        lines.append((f"Ambiguas (revisar): {', '.join(map(str, result.ambiguous))}", 18, True,
                      orange))
    if result.blank:
        lines.append((f"En blanco: {', '.join(map(str, result.blank))}", 18, False, muted))
    if others:
        lines.append((f"Esta matrícula ya tenía {len(others)} resultado(s):", 17, True, red))
        lines.append(("elige cuál cuenta con cli.results", 17, False, red))
    lines.append(("Guardado en la BD" if saved else "Sin guardar (--dry-run)", 17, False, muted))
    put_text(out, lines, review.shape[1] + 30, 30)

    # Botones
    by = target_h - 150
    for i, (label, color) in enumerate([("[N]  Calificar otro", green), ("[Q]  Salir", muted)]):
        y = by + i * 64
        cv2.rectangle(out, (review.shape[1] + 30, y), (out.shape[1] - 30, y + 50), color, -1)
        put_text(out, [(label, 24, True, (255, 255, 255))], review.shape[1] + 50, y + 11)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--camera", default="0",
                        help="Número de cámara (0 = la de la computadora) o URL de video")
    parser.add_argument("--exam", help="Id del examen, por si el QR no se puede leer")
    parser.add_argument("--page", default="letter", choices=list(PAGE_SIZES_MM),
                        help="Tamaño de página, solo si se usa --exam sin QR")
    parser.add_argument("--dry-run", action="store_true", help="Calificar sin guardar en la BD")
    add_verbose_flag(parser)
    args = parser.parse_args()
    setup_logging(args.verbose)
    if args.verbose == 1:
        # Con -v solo el flujo del escaneo; las funciones de core/ se llaman
        # en cada cuadro y llenarían la pantalla (p. ej. "no se detectó
        # ningún marcador" es normal mientras no hay hoja). Con -vv se ve todo.
        logging.getLogger("core").setLevel(logging.ERROR)
        logging.getLogger("core.live").setLevel(logging.INFO)
    logger.info("inicio: camera=%s exam=%s dry_run=%s", args.camera, args.exam, args.dry_run)

    session = open_session()
    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
    graded_count = 0
    try:
        while True:
            scanner = LiveScanner(lambda exam_id: repository.get_exam(session, exam_id),
                                  fallback_exam_id=args.exam, fallback_page=args.page)
            status = scan_one(args.camera, scanner)

            stem = "vivo_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            result_id, image_path, others = save_graded(session, status.graded, stem,
                                                        args.dry_run)
            capture_name = (f"{stem}_captura.jpg" if args.dry_run
                            else f"{result_id}_captura.jpg")
            cv2.imwrite(str(Path(image_path).with_name(capture_name)), status.capture)
            print_result("cámara", status.graded, result_id, image_path, others)
            graded_count += 1

            show(draw_result(cv2.imread(str(image_path)), status, others, not args.dry_run))
            while True:
                key = wait_key(0)
                if key in (ord("q"), 27):
                    raise Quit
                if key == ord("n"):
                    logger.info("calificar otro")
                    break
    except Quit:
        pass
    finally:
        cv2.destroyAllWindows()
    print(f"\n{graded_count} hoja(s) calificada(s).")
    logger.info("fin: %d hojas", graded_count)


if __name__ == "__main__":
    main()
