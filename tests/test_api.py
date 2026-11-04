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
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy.pool import StaticPool

import storage.saving
from api.auth import Auth
from api.main import create_app
from core import live
from storage import repository
from storage.db import init_db
from core.alignment import detect_corner_markers
from core.qr import read_qr
from core.layout import build_layout
from models.exam import Exam
from tests.helpers import fill_bubbles, rasterize, simulate_photo
from tests.test_detection import EXAM, STUDENT_ID, make_sheet

SHEET = make_sheet()


def memory_engine():
    """SQLite en memoria para la API. StaticPool: una sola conexión, para que
    todos los hilos vean la misma BD. Con las FK activas (borrado en cascada),
    como en `storage.db.make_engine`."""
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return engine


def jpg(image: np.ndarray) -> bytes:
    return cv2.imencode(".jpg", image)[1].tobytes()


@pytest.fixture
def db():
    engine = memory_engine()
    init_db(engine)
    factory = sessionmaker(engine)
    with factory() as s:
        repository.create_exam(s, EXAM)
    return factory


@pytest.fixture
def client(db, tmp_path, monkeypatch):
    monkeypatch.setattr(storage.saving, "RESULTS_IMG_DIR", tmp_path)
    return TestClient(create_app(db, web_dist=None))


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
    engine = memory_engine()
    init_db(engine)
    factory = sessionmaker(engine)
    with factory() as s:
        repository.create_exam(s, EXAM, owner_id=USER_A)
    monkeypatch.setattr(storage.saving, "RESULTS_IMG_DIR", tmp_path)
    auth = Auth(SUPABASE_URL, "sb_publishable_prueba", signing_key=KEY.public_key())
    return TestClient(create_app(factory, auth, web_dist=None)), factory


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
    engine = memory_engine()
    init_db(engine)
    client = TestClient(create_app(sessionmaker(engine),
                                   Auth(SUPABASE_URL, jwt_secret=secret), web_dist=None))
    now = int(time.time())
    good = jwt.encode({"sub": str(USER_A), "aud": "authenticated", "iss": f"{SUPABASE_URL}/auth/v1",
                       "exp": now + 600}, secret, algorithm="HS256")
    bad = jwt.encode({"sub": str(USER_A), "aud": "authenticated", "iss": f"{SUPABASE_URL}/auth/v1",
                      "exp": now + 600}, "otro-secreto-de-al-menos-32-bytes-xx", algorithm="HS256")
    assert client.get("/api/me", headers={"Authorization": f"Bearer {good}"}).status_code == 200
    assert client.get("/api/me", headers={"Authorization": f"Bearer {bad}"}).status_code == 401



# --- Mis exámenes: crear, editar, borrar, PDF ---

def new_exam(n_questions: int = 6, weights=(60, 40), **kw) -> dict:
    """Un examen como lo manda el editor de la web."""
    half = n_questions // 2
    sections = [
        {"id": f"s{i + 1}", "name": f"Sección {i + 1}", "weight": w,
         "questions": [{"number": start + q + 1, "correct_answer": "ABCD"[q % 4], "points": 1}
                       for q in range(half)]}
        for i, (w, start) in enumerate(zip(weights, (0, half)))
    ]
    return {"name": "Química - Parcial 2", "num_options": 4, "student_id_digits": 5,
            "sections": sections, **kw}


def test_create_and_get_exam(auth_client):
    client, factory = auth_client
    resp = client.post("/api/exams", json=new_exam(), headers=as_user(USER_A))
    assert resp.status_code == 201
    exam_id = resp.json()["id"]
    assert len(exam_id) == 12  # generado
    exam = client.get(f"/api/exams/{exam_id}", headers=as_user(USER_A)).json()
    assert exam["name"] == "Química - Parcial 2" and exam["locked"] is False
    assert [len(s["questions"]) for s in exam["sections"]] == [3, 3]
    ids = [e["id"] for e in client.get("/api/exams", headers=as_user(USER_A)).json()]
    assert exam_id in ids
    assert client.get("/api/exams", headers=as_user(USER_B)).json() == []


@pytest.mark.parametrize("body, text", [
    (new_exam(weights=(60, 30)), "suman"),
    (new_exam(num_options=2), "no está en"),  # hay respuestas C y D
    (new_exam(n_questions=400), "no cabe"),
])
def test_invalid_exam_is_rejected(auth_client, body, text):
    client, _ = auth_client
    resp = client.post("/api/exams", json=body, headers=as_user(USER_A))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert text in detail["message"] + " ".join(detail["problems"])


def test_other_user_cannot_touch_my_exam(auth_client):
    client, _ = auth_client
    exam_id = client.post("/api/exams", json=new_exam(), headers=as_user(USER_A)).json()["id"]
    b = as_user(USER_B)
    assert client.get(f"/api/exams/{exam_id}", headers=b).status_code == 404
    assert client.put(f"/api/exams/{exam_id}", json=new_exam(), headers=b).status_code == 404
    assert client.delete(f"/api/exams/{exam_id}", headers=b).status_code == 404
    assert client.get(f"/api/exams/{exam_id}/sheet.pdf", headers=b).status_code == 404
    assert client.get(f"/api/exams/{exam_id}", headers=as_user(USER_A)).status_code == 200


def test_edit_with_results_rescores_but_keeps_structure(auth_client):
    """Con resultados: cambiar respuestas recalifica; cambiar preguntas, no se puede."""
    client, _ = auth_client
    a = as_user(USER_A)
    photo = jpg(simulate_photo(SHEET, seed=2))
    client.post("/api/grade", files={"photo": ("foto.jpg", photo, "image/jpeg")}, headers=a)
    exam = client.get(f"/api/exams/{EXAM.id}", headers=a).json()
    assert exam["locked"] and exam["results"] == 1

    body = {k: exam[k] for k in ("name", "num_options", "student_id_digits", "sections")}
    body["sections"][0]["questions"][0]["correct_answer"] = "D"
    resp = client.put(f"/api/exams/{EXAM.id}", json=body, headers=a)
    assert resp.status_code == 200 and resp.json()["rescored"] == 1

    body["sections"][0]["questions"].pop()
    resp = client.put(f"/api/exams/{EXAM.id}", json=body, headers=a)
    assert resp.status_code == 409 and "no se puede cambiar" in resp.json()["detail"]


def test_delete_exam_with_results(auth_client):
    client, factory = auth_client
    a = as_user(USER_A)
    photo = jpg(simulate_photo(SHEET, seed=2))
    client.post("/api/grade", files={"photo": ("foto.jpg", photo, "image/jpeg")}, headers=a)
    resp = client.delete(f"/api/exams/{EXAM.id}", headers=a)
    assert resp.json()["deleted_results"] == 1
    assert client.get(f"/api/exams/{EXAM.id}", headers=a).status_code == 404
    with factory() as s:
        assert repository.count_results(s, EXAM.id) == 0


@pytest.mark.parametrize("page", ["letter", "a4"])
def test_sheet_pdf_has_the_exam_qr(auth_client, page):
    client, _ = auth_client
    a = as_user(USER_A)
    exam_id = client.post("/api/exams", json=new_exam(), headers=a).json()["id"]
    resp = client.get(f"/api/exams/{exam_id}/sheet.pdf?page={page}", headers=a)
    assert resp.status_code == 200 and resp.content.startswith(b"%PDF")
    assert "quimica_parcial_2" in resp.headers["content-disposition"]
    image = rasterize(resp.content)
    qr = read_qr(image, detect_corner_markers(image))
    assert qr.exam_id == exam_id and qr.page == page


def test_preview_of_unsaved_exam(auth_client):
    client, _ = auth_client
    resp = client.post("/api/exams/preview?page=a4", json=new_exam(), headers=as_user(USER_A))
    assert resp.status_code == 200 and resp.content.startswith(b"\x89PNG")
    resp = client.post("/api/exams/preview", json=new_exam(n_questions=400),
                       headers=as_user(USER_A))
    assert resp.status_code == 422


def test_serves_react_build_with_route_fallback(db, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id=root>react</div>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    client = TestClient(create_app(db, web_dist=dist))
    assert "react" in client.get("/").text
    assert "react" in client.get("/examenes/nuevo").text  # ruta de React
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert "react" in client.get("/../../etc/passwd").text  # no se sale de dist
    assert client.get("/api/no-existe").status_code == 404
    assert client.get("/api/exams").status_code == 200
    assert "Calificador" in client.get("/prueba").text


def test_a4_sheet_is_graded_with_a4_layout(auth_client):
    """Antes el QR no decía la página y una hoja A4 se leía con el layout de
    carta. Se contesta una hoja A4 y se califica sin indicar la página."""
    client, _ = auth_client
    a = as_user(USER_A)
    exam_id = client.post("/api/exams", json=new_exam(), headers=a).json()["id"]
    exam = client.get(f"/api/exams/{exam_id}", headers=a).json()
    sheet = rasterize(client.get(f"/api/exams/{exam_id}/sheet.pdf?page=a4", headers=a).content)
    layout = build_layout(Exam.from_dict(exam), page="a4")
    answers = {1: "B", 2: "C", 3: "A", 4: "D", 5: "B", 6: "A"}
    fill_bubbles(sheet, layout, [layout.questions[q]["ABCD".index(x)] for q, x in answers.items()])
    fill_bubbles(sheet, layout, [layout.student_id[i][int(d)] for i, d in enumerate("40213")])
    photo = jpg(simulate_photo(sheet, seed=7))
    r = client.post("/api/grade", files={"photo": ("a4.jpg", photo, "image/jpeg")},
                    headers=a).json()["result"]
    assert r["student_id"] == "40213" and r["ambiguous"] == [] and r["blank"] == []
