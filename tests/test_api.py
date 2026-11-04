"""
API (la que usa la página del celular) con BD en memoria: calificar una
foto subida, un escaneo en vivo cuadro por cuadro y, con sesión de
Supabase, que cada usuario solo vea y use lo suyo. Los tokens se firman
aquí con una llave de prueba, sin conectarse a Supabase.
"""

import time
import uuid

import cv2
import jwt
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy.pool import StaticPool

import storage.saving
from api.auth import Auth
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
    monkeypatch.setattr(storage.saving, "RESULTS_IMG_DIR", tmp_path)
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


# --- Con sesión (Supabase) ---

SUPABASE_URL = "https://prueba.supabase.co"
KEY = ec.generate_private_key(ec.SECP256R1())
USER_A, USER_B = uuid.uuid4(), uuid.uuid4()


def token(user: uuid.UUID, key=KEY, expires_in: int = 3600, aud: str = "authenticated") -> str:
    now = int(time.time())
    claims = {"sub": str(user), "aud": aud, "iss": f"{SUPABASE_URL}/auth/v1",
              "iat": now, "exp": now + expires_in, "email": f"{user.hex[:6]}@prueba.com"}
    return jwt.encode(claims, key, algorithm="ES256")


def as_user(user: uuid.UUID, **kw) -> dict:
    return {"Authorization": f"Bearer {token(user, **kw)}"}


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    """El examen es del usuario A."""
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    init_db(engine)
    factory = sessionmaker(engine)
    with factory() as s:
        repository.create_exam(s, EXAM, owner_id=USER_A)
    monkeypatch.setattr(storage.saving, "RESULTS_IMG_DIR", tmp_path)
    auth = Auth(SUPABASE_URL, "sb_publishable_prueba", signing_key=KEY.public_key())
    return TestClient(create_app(factory, auth)), factory


def test_config_says_login_is_needed(auth_client):
    client, _ = auth_client
    assert client.get("/api/config").json() == {
        "auth": True, "supabase_url": SUPABASE_URL, "publishable_key": "sb_publishable_prueba"}
    assert client.get("/").status_code == 200  # la página no pide sesión


def test_local_mode_needs_no_login(client):
    assert client.get("/api/config").json() == {"auth": False}


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer basura"},
    as_user(USER_A, expires_in=-60),  # vencido
    as_user(USER_A, key=ec.generate_private_key(ec.SECP256R1())),  # otro proyecto
    as_user(USER_A, aud="anon"),  # sin sesión iniciada
])
def test_invalid_session_is_rejected(auth_client, headers):
    client, _ = auth_client
    assert client.get("/api/exams", headers=headers).status_code == 401


def test_each_user_sees_only_their_exams(auth_client):
    client, _ = auth_client
    assert client.get("/api/me", headers=as_user(USER_A)).json()["user_id"] == str(USER_A)
    assert [e["id"] for e in client.get("/api/exams", headers=as_user(USER_A)).json()] == [EXAM.id]
    assert client.get("/api/exams", headers=as_user(USER_B)).json() == []


def test_result_is_saved_for_the_user(auth_client):
    client, factory = auth_client
    photo = jpg(simulate_photo(SHEET, seed=2))
    resp = client.post("/api/grade", files={"photo": ("foto.jpg", photo, "image/jpeg")},
                       headers=as_user(USER_A))
    assert resp.status_code == 200
    with factory() as s:
        row = repository.get_result(s, uuid.UUID(resp.json()["result"]["result_id"]))
        assert row.owner_id == USER_A


def test_other_user_cannot_grade_with_my_exam(auth_client):
    """B tiene la hoja impresa del examen de A: para B ese examen no existe."""
    client, factory = auth_client
    photo = jpg(simulate_photo(SHEET, seed=2))
    resp = client.post("/api/grade", files={"photo": ("foto.jpg", photo, "image/jpeg")},
                       headers=as_user(USER_B))
    assert resp.status_code == 422 and "no está registrado" in resp.json()["detail"]
    with factory() as s:
        assert repository.count_results(s, EXAM.id) == 0


def test_other_user_cannot_use_my_scan(auth_client):
    client, _ = auth_client
    scan_id = client.post("/api/scan/sessions", json={}, headers=as_user(USER_A)).json()["scan_id"]
    frame = jpg(simulate_photo(SHEET, seed=0))
    url = f"/api/scan/sessions/{scan_id}/frame"
    files = {"frame": ("f.jpg", frame, "image/jpeg")}
    assert client.post(url, files=files, headers=as_user(USER_B)).status_code == 404
    assert client.delete(f"/api/scan/sessions/{scan_id}", headers=as_user(USER_B)).status_code == 404
    assert client.post(url, files=files, headers=as_user(USER_A)).json()["state"] == live.READING


def test_legacy_hs256_secret(tmp_path, monkeypatch):
    """Proyectos de Supabase con la llave "legacy": firman con un secreto."""
    secret = "secreto-de-prueba-de-al-menos-32-bytes"
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    init_db(engine)
    client = TestClient(create_app(sessionmaker(engine),
                                   Auth(SUPABASE_URL, jwt_secret=secret)))
    now = int(time.time())
    good = jwt.encode({"sub": str(USER_A), "aud": "authenticated", "iss": f"{SUPABASE_URL}/auth/v1",
                       "exp": now + 600}, secret, algorithm="HS256")
    bad = jwt.encode({"sub": str(USER_A), "aud": "authenticated", "iss": f"{SUPABASE_URL}/auth/v1",
                      "exp": now + 600}, "otro-secreto-de-al-menos-32-bytes-xx", algorithm="HS256")
    assert client.get("/api/me", headers={"Authorization": f"Bearer {good}"}).status_code == 200
    assert client.get("/api/me", headers={"Authorization": f"Bearer {bad}"}).status_code == 401
