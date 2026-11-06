"""
Imágenes de revisión: en disco o en Supabase Storage. Storage se simula con
un transporte de httpx (un dict en memoria), sin conectarse a Supabase.
"""

import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import storage.saving
from api.auth import Auth
from api.main import create_app
from storage import repository
from storage.db import init_db
from storage.images import ImageStoreError, LocalImageStore, SupabaseImageStore
from tests.helpers import simulate_photo
from tests.test_api import (KEY, SHEET, SUPABASE_URL, USER_A, as_user, jpg, memory_engine)
from tests.test_detection import EXAM

SECRET = "sb_secret_prueba"


class FakeStorage:
    """Lo mínimo de la API de Supabase Storage que usamos."""

    def __init__(self, fail_uploads=False):
        self.objects: dict[str, bytes] = {}
        self.buckets: dict[str, dict] = {}
        self.fail_uploads = fail_uploads
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["apikey"] == SECRET
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        path = request.url.path.removeprefix("/storage/v1")
        if path.startswith("/bucket"):
            if request.method == "GET":
                bucket = self.buckets.get(path.removeprefix("/bucket/"))
                return httpx.Response(200, json=bucket) if bucket else httpx.Response(404)
            data = json.loads(request.content)
            self.buckets[data["id"]] = data
            return httpx.Response(200, json={"name": data["id"]})
        if request.method == "POST":
            if self.fail_uploads:
                return httpx.Response(500, json={"error": "caído"})
            assert request.headers["x-upsert"] == "true"
            self.objects[path.removeprefix("/object/")] = request.content
            return httpx.Response(200, json={"Key": path})
        if request.method == "GET":
            key = path.removeprefix("/object/authenticated/")
            if key not in self.objects:
                return httpx.Response(400, json={"error": "not_found"})
            return httpx.Response(200, content=self.objects[key])
        if request.method == "DELETE":
            bucket = path.removeprefix("/object/")
            for key in json.loads(request.content)["prefixes"]:
                self.objects.pop(f"{bucket}/{key}", None)
            return httpx.Response(200, json=[])
        return httpx.Response(405)

    def store(self) -> SupabaseImageStore:
        return SupabaseImageStore(SUPABASE_URL, SECRET, "revisiones", transport=self.transport)


def test_local_store(tmp_path):
    store = LocalImageStore(tmp_path)
    store.put("a/b/c.jpg", b"123")
    assert store.get("a/b/c.jpg") == b"123"
    store.delete(["a/b/c.jpg", "a/b/no-existe.jpg"])
    assert store.get("a/b/c.jpg") is None
    with pytest.raises(ImageStoreError):
        store.get("../../etc/passwd")
    old = tmp_path / "vieja.png"  # hojas de antes: ruta absoluta en image_path
    old.write_bytes(b"png")
    assert LocalImageStore(tmp_path / "otro").get(str(old)) == b"png"


def test_supabase_store_roundtrip():
    fake = FakeStorage()
    store = fake.store()
    store.ensure_ready()
    assert fake.buckets["revisiones"]["public"] is False  # se crea privado
    store.ensure_ready()  # ya existe: no pasa nada

    store.put("u/e/r.jpg", b"jpg")
    assert fake.objects == {"revisiones/u/e/r.jpg": b"jpg"}
    assert store.get("u/e/r.jpg") == b"jpg"
    assert store.get("u/e/otra.jpg") is None
    store.delete(["u/e/r.jpg"])
    assert fake.objects == {}


@pytest.fixture
def storage_client(tmp_path, monkeypatch):
    def make(fake: FakeStorage):
        engine = memory_engine()
        init_db(engine)
        factory = sessionmaker(engine)
        with factory() as s:
            repository.create_exam(s, EXAM, owner_id=USER_A)
        monkeypatch.setattr(storage.saving, "RESULTS_IMG_DIR", tmp_path)
        auth = Auth(SUPABASE_URL, "sb_publishable_prueba", signing_key=KEY.public_key())
        app = create_app(factory, auth, web_dist=None, images=fake.store())
        return TestClient(app), factory
    return make


def grade(client) -> dict:
    photo = jpg(simulate_photo(SHEET, seed=2))
    resp = client.post("/api/grade", files={"photo": ("f.jpg", photo, "image/jpeg")},
                       headers=as_user(USER_A))
    assert resp.status_code == 200
    return resp.json()["result"]


def test_graded_sheet_goes_to_storage(storage_client, tmp_path):
    fake = FakeStorage()
    client, factory = storage_client(fake)
    r = grade(client)
    key = f"{USER_A}/{EXAM.id}/{r['result_id']}.jpg"
    assert list(fake.objects) == [f"revisiones/{key}"]
    assert r["review_image"].startswith("data:image/jpeg;base64,")
    with factory() as s:
        assert repository.get_result(s, uuid.UUID(r["result_id"])).image_path == key
    assert not list(tmp_path.rglob("*.jpg"))  # nada en disco

    image = client.get(f"/api/results/{r['result_id']}/review.jpg", headers=as_user(USER_A))
    assert image.status_code == 200 and image.content.startswith(b"\xff\xd8")

    client.delete(f"/api/results/{r['result_id']}", headers=as_user(USER_A))
    assert fake.objects == {}


def test_deleting_exam_deletes_its_images(storage_client):
    fake = FakeStorage()
    client, _ = storage_client(fake)
    grade(client)
    grade(client)
    assert len(fake.objects) == 2
    assert client.delete(f"/api/exams/{EXAM.id}", headers=as_user(USER_A)).json() \
        == {"id": EXAM.id, "deleted_results": 2}
    assert fake.objects == {}


def test_storage_down_still_saves_the_result(storage_client):
    client, factory = storage_client(FakeStorage(fail_uploads=True))
    r = grade(client)
    assert r["saved"] and r["review_image"]  # la nota y la imagen llegan igual
    detail = client.get(f"/api/results/{r['result_id']}", headers=as_user(USER_A)).json()
    assert detail["has_image"] is False
