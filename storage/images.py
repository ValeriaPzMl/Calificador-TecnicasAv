"""
Dónde se guardan las imágenes de revisión de las hojas calificadas.

- **Supabase Storage** (si en `.env` están `SUPABASE_URL` y
  `SUPABASE_SECRET_KEY`): un bucket privado (`SUPABASE_BUCKET`, por defecto
  "revisiones"). Solo el servidor lo usa, con la llave secreta; el navegador
  nunca la ve y pide las imágenes a nuestra API, que revisa que la hoja sea
  del usuario.
- **Disco** (modo local, o si falta la llave secreta):
  `data/processed/results/`.

En la BD, `results.image_path` guarda la llave de la imagen dentro del
almacén: `<owner_id>/<exam_id>/<result_id>.jpg`. Las hojas viejas guardaban
la ruta absoluta en disco; el almacén local las sigue leyendo y
`scripts/upload_images.py` las sube a Storage.
"""

import logging
import os
from pathlib import Path
from urllib.parse import quote

import httpx

from .db import PROJECT_DIR  # también carga el .env

LOCAL_DIR = PROJECT_DIR / "data" / "processed" / "results"
DEFAULT_BUCKET = "revisiones"
TIMEOUT_S = 20

logger = logging.getLogger(__name__)


class ImageStoreError(Exception):
    pass


def image_key(owner_id, exam_id: str, result_id) -> str:
    return f"{owner_id}/{exam_id}/{result_id}.jpg"


def capture_key(key: str, result_id) -> str:
    """La captura de la cámara en vivo va junto a su imagen de revisión."""
    return key.rsplit("/", 1)[0] + f"/{result_id}_captura.jpg" if "/" in key else key


class LocalImageStore:
    kind = "disco"

    def __init__(self, root: Path = LOCAL_DIR):
        self.root = Path(root)

    def path(self, key: str) -> Path:
        path = Path(key)
        if path.is_absolute():  # hojas de antes: ruta completa en disco
            return path
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ImageStoreError(f"Llave de imagen inválida: {key}")
        return path

    def put(self, key: str, data: bytes, content_type: str = "image/jpeg") -> None:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        logger.info("imagen guardada en disco: %s (%d bytes)", path, len(data))

    def get(self, key: str) -> bytes | None:
        path = self.path(key)
        if not path.is_file():
            logger.warning("no existe la imagen %s", path)
            return None
        logger.debug("imagen leída de disco: %s", path)
        return path.read_bytes()

    def delete(self, keys: list[str]) -> None:
        for key in keys:
            path = self.path(key)
            if path.exists():
                path.unlink()
                logger.debug("imagen borrada: %s", path)
        logger.info("%d imagen(es) por borrar en disco", len(keys))

    def ensure_ready(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)


class SupabaseImageStore:
    """Bucket privado de Supabase Storage, por su API REST."""
    kind = "Supabase Storage"

    def __init__(self, supabase_url: str, secret_key: str, bucket: str = DEFAULT_BUCKET,
                 transport: httpx.BaseTransport | None = None):
        self.bucket = bucket
        self.client = httpx.Client(
            base_url=supabase_url.rstrip("/") + "/storage/v1",
            headers={"apikey": secret_key, "Authorization": f"Bearer {secret_key}"},
            timeout=TIMEOUT_S,
            transport=transport,
        )

    def _object(self, key: str) -> str:
        return f"/object/{self.bucket}/{quote(key)}"

    def put(self, key: str, data: bytes, content_type: str = "image/jpeg") -> None:
        resp = self.client.post(self._object(key), content=data,
                                headers={"Content-Type": content_type, "x-upsert": "true"})
        self._check(resp, f"subir {key}")
        logger.info("imagen subida a Storage: %s/%s (%d bytes)", self.bucket, key, len(data))

    def get(self, key: str) -> bytes | None:
        resp = self.client.get(f"/object/authenticated/{self.bucket}/{quote(key)}")
        if resp.status_code in (400, 404):  # Storage responde 400 "not_found" a veces
            logger.warning("no existe la imagen %s/%s en Storage (%d)", self.bucket, key,
                           resp.status_code)
            return None
        self._check(resp, f"bajar {key}")
        logger.debug("imagen bajada de Storage: %s (%d bytes)", key, len(resp.content))
        return resp.content

    def delete(self, keys: list[str]) -> None:
        if not keys:
            return
        resp = self.client.request("DELETE", f"/object/{self.bucket}", json={"prefixes": keys})
        self._check(resp, f"borrar {len(keys)} imagen(es)")
        logger.info("%d imagen(es) borradas de Storage", len(keys))

    def ensure_ready(self) -> None:
        """Crea el bucket (privado) si no existe."""
        resp = self.client.get(f"/bucket/{self.bucket}")
        if resp.status_code == 200:
            if resp.json().get("public"):
                logger.warning("el bucket '%s' es PÚBLICO: cualquiera con el enlace ve las hojas",
                               self.bucket)
            logger.info("bucket '%s' listo", self.bucket)
            return
        logger.info("creando bucket privado '%s'", self.bucket)
        resp = self.client.post("/bucket", json={"id": self.bucket, "name": self.bucket,
                                                 "public": False})
        self._check(resp, f"crear el bucket {self.bucket}")

    @staticmethod
    def _check(resp: httpx.Response, what: str) -> None:
        if resp.is_success:
            return
        logger.error("Storage: no se pudo %s: %d %s", what, resp.status_code, resp.text[:200])
        raise ImageStoreError(f"Supabase Storage: no se pudo {what} ({resp.status_code}).")


def image_store_from_env():
    url = os.environ.get("SUPABASE_URL") or None
    secret = (os.environ.get("SUPABASE_SECRET_KEY")
              or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or None)
    if url and secret:
        bucket = os.environ.get("SUPABASE_BUCKET") or DEFAULT_BUCKET
        logger.info("imágenes de revisión en Supabase Storage (bucket '%s')", bucket)
        return SupabaseImageStore(url, secret, bucket)
    if url:
        logger.warning("hay SUPABASE_URL pero no SUPABASE_SECRET_KEY: las imágenes se "
                       "guardan en disco (%s)", LOCAL_DIR)
    else:
        logger.info("imágenes de revisión en disco (%s)", LOCAL_DIR)
    return LocalImageStore()


_store = None


def get_image_store():
    """El almacén configurado (se crea una vez)."""
    global _store
    if _store is None:
        _store = image_store_from_env()
    return _store
