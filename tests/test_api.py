"""
API (la que usa la página del celular) con BD en memoria: calificar una
foto subida y un escaneo en vivo cuadro por cuadro.
"""

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import cli.grade
from api.main import create_app
from core import live
from storage import repository
from storage.db import init_db
from tests.helpers import simulate_photo
from tests.test_detection import EXAM, STUDENT_ID, make_sheet

SHEET = make_sheet()


def jpg(image: np.ndarray) -> bytes:
    return cv2.imencode(".jpg", image)[1].tobytes()


@pytest.fixture
def db():
    # StaticPool: una sola conexión, para que todos los hilos vean la misma BD en memoria
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    init_db(engine)
    factory = sessionmaker(engine)
    with factory() as s:
        repository.create_exam(s, EXAM)
    return factory


@pytest.fixture
def client(db, tmp_path, monkeypatch):
    monkeypatch.setattr(cli.grade, "RESULTS_IMG_DIR", tmp_path)
    return TestClient(create_app(db))


def count(db) -> int:
    with db() as s:
        return repository.count_results(s, EXAM.id)


def test_index_and_exams(client):
    assert "Calificador" in client.get("/").text
    exams = client.get("/api/exams").json()
    assert [e["id"] for e in exams] == [EXAM.id]


def test_grade_uploaded_photo(client, db):
    photo = jpg(simulate_photo(SHEET, seed=2))
    resp = client.post("/api/grade", files={"photo": ("foto.jpg", photo, "image/jpeg")})
    assert resp.status_code == 200
    r = resp.json()["result"]
    assert r["saved"] and r["result_id"]
    assert r["student_id"] == STUDENT_ID and r["exam"]["id"] == EXAM.id
    assert r["review_image"].startswith("data:image/jpeg;base64,")
    assert count(db) == 1


def test_grade_dry_run_does_not_save(client, db):
    photo = jpg(simulate_photo(SHEET, seed=2))
    resp = client.post("/api/grade", files={"photo": ("foto.jpg", photo, "image/jpeg")},
                       data={"dry_run": "true"})
    assert resp.status_code == 200 and not resp.json()["result"]["saved"]
    assert count(db) == 0


def test_grade_photo_without_sheet(client):
    resp = client.post("/api/grade",
                       files={"photo": ("x.jpg", jpg(np.full((600, 800, 3), 120, np.uint8)),
                                        "image/jpeg")})
    assert resp.status_code == 422 and resp.json()["detail"]


def test_grade_not_an_image(client):
    resp = client.post("/api/grade", files={"photo": ("x.jpg", b"hola", "image/jpeg")})
    assert resp.status_code == 400


def test_live_scan_until_ready(client, db):
    scan_id = client.post("/api/scan/sessions", json={}).json()["scan_id"]
    url = f"/api/scan/sessions/{scan_id}/frame"

    def send(seed):
        frame = jpg(simulate_photo(SHEET, seed=seed))
        return client.post(url, files={"frame": ("f.jpg", frame, "image/jpeg")}).json()

    states = [send(seed) for seed in range(live.STABLE_FRAMES)]
    assert [s["state"] for s in states[:-1]] == [live.READING] * (live.STABLE_FRAMES - 1)
    assert len(states[0]["corners"]) == 4
    done = states[-1]
    assert done["state"] == live.READY
    assert done["result"]["student_id"] == STUDENT_ID

    again = send(99)  # un cuadro de más (llegó tarde): no se guarda dos veces
    assert again["result"]["result_id"] == done["result"]["result_id"]
    assert count(db) == 1

    client.delete(f"/api/scan/sessions/{scan_id}")
    assert client.post(url, files={"frame": ("f.jpg", b"x", "image/jpeg")}).status_code == 404


def test_live_scan_without_sheet_keeps_searching(client):
    scan_id = client.post("/api/scan/sessions", json={"dry_run": True}).json()["scan_id"]
    frame = jpg(np.full((720, 1280, 3), 120, np.uint8))
    status = client.post(f"/api/scan/sessions/{scan_id}/frame",
                         files={"frame": ("f.jpg", frame, "image/jpeg")}).json()
    assert status["state"] == live.SEARCHING and "result" not in status
