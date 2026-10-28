"""
Escaneo en vivo con cuadros simulados: cada "cuadro" es la misma hoja
deformada con otra semilla (como una cámara que se mueve un poco).
"""

import cv2
import numpy as np
import pytest

from core import live
from core.detection import bubble_fill
from core.live import BLURRY, NO_QR, READING, READY, SEARCHING, UNKNOWN_EXAM, LiveScanner
from tests.helpers import RASTER_DPI, simulate_photo
from tests.test_detection import AMBIGUOUS, ANSWERED, BLANK, EXAM, LAYOUT, STUDENT_ID, make_sheet

FIND = {EXAM.id: EXAM}.get
SHEET = make_sheet()


def frame(seed: int, sheet=SHEET) -> np.ndarray:
    return simulate_photo(sheet, seed=seed)


def no_sheet() -> np.ndarray:
    return np.full((720, 1280, 3), 120, np.uint8)


def test_ready_after_stable_frames():
    scanner = LiveScanner(FIND)
    states = [scanner.process(frame(seed)).state for seed in range(live.STABLE_FRAMES)]
    assert states[:-1] == [READING] * (live.STABLE_FRAMES - 1)
    assert states[-1] == READY

    status = scanner.process(frame(99))  # ya terminó: no sigue procesando
    assert status.state == READY
    r = status.graded.reading
    assert r.student_id == STUDENT_ID
    assert {q: a for q, a in r.answers.items() if a} == ANSWERED
    assert r.ambiguous == AMBIGUOUS and r.blank == BLANK
    assert status.capture is not None and status.graded.result.max_score == 100


def test_no_sheet_keeps_searching():
    scanner = LiveScanner(FIND)
    status = scanner.process(no_sheet())
    assert status.state == SEARCHING
    assert status.corners == {}


def test_losing_the_sheet_restarts_count():
    scanner = LiveScanner(FIND)
    scanner.process(frame(0))
    scanner.process(frame(1))
    assert scanner.process(no_sheet()).state == SEARCHING
    status = scanner.process(frame(2))
    assert status.state == READING and status.progress == 1


def test_changed_reading_restarts_count():
    other = make_sheet(student_id="999999")
    scanner = LiveScanner(FIND)
    scanner.process(frame(0))
    scanner.process(frame(1))
    status = scanner.process(frame(2, sheet=other))
    assert status.state == READING and status.progress == 1


def test_blurry_frame_is_skipped(monkeypatch):
    monkeypatch.setattr(live, "MIN_SHARPNESS", 1e9)  # todo cuadro cuenta como borroso
    status = LiveScanner(FIND).process(frame(0))
    assert status.state == BLURRY


def test_unknown_exam():
    status = LiveScanner(lambda _: None).process(frame(0))
    assert status.state == UNKNOWN_EXAM and status.exam_id == EXAM.id


def test_qr_unreadable_uses_fallback_after_some_frames():
    sheet = SHEET.copy()
    px = RASTER_DPI / 25.4
    box = LAYOUT.qr_box
    cv2.rectangle(sheet, (round(box.x * px), round(box.y * px)),
                  (round((box.x + box.w) * px), round((box.y + box.h) * px)), (255, 255, 255), -1)

    assert LiveScanner(FIND).process(frame(0, sheet)).state == NO_QR

    scanner = LiveScanner(FIND, fallback_exam_id=EXAM.id)
    states = [scanner.process(frame(s, sheet)).state
              for s in range(live.QR_TRIES_BEFORE_FALLBACK + live.STABLE_FRAMES - 1)]
    assert states[0] == NO_QR and states[-1] == READY
    assert not scanner.process(frame(0, sheet)).graded.qr_read


def test_big_frames_are_reduced_and_corners_in_frame_pixels():
    big = cv2.resize(frame(0), None, fx=1.6, fy=1.6)
    status = LiveScanner(FIND).process(big)
    assert status.state == READING
    xs = [x for x, _ in status.corners.values()]
    assert max(xs) > live.MAX_SIDE  # esquinas en px del cuadro original, no del reducido


def test_bubble_fill_local_mask_matches_full_mask():
    """La máscara local de bubble_fill da lo mismo que una del tamaño de
    toda la hoja (la versión anterior, más lenta)."""
    from core.alignment import align_sheet
    from core.preprocessing import to_grayscale

    gray = to_grayscale(align_sheet(frame(0), size=LAYOUT.canonical_size))
    for b in LAYOUT.all_bubbles()[::7]:
        cx, cy, r = LAYOUT.bubble_px(b)
        mask = np.zeros(gray.shape, np.uint8)
        cv2.circle(mask, (round(cx), round(cy)), max(1, round(r * 0.65)), 255, -1)
        inner = cv2.mean(gray, mask=mask)[0]
        half = int(r * 2.2)
        x0, y0 = max(0, int(cx) - half), max(0, int(cy) - half)
        paper = float(np.percentile(gray[y0:int(cy) + half, x0:int(cx) + half], 90))
        assert bubble_fill(gray, LAYOUT, b) == pytest.approx(min(1, max(0, 1 - inner / paper)))
