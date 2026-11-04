"""
Quién hace cada petición: valida el token de inicio de sesión de Supabase.

La página inicia sesión directo con Supabase (correo y contraseña) y manda
el token en cada petición: `Authorization: Bearer <access_token>`. Aquí se
revisa que lo haya firmado nuestro proyecto de Supabase, que no esté
vencido y que sea de un usuario con sesión (`aud = "authenticated"`). El
usuario es `sub` (su uuid en `auth.users`), y es el `owner_id` de todo lo
que crea.

- Proyectos nuevos de Supabase firman con llaves asimétricas (ES256/RS256):
  las públicas se bajan de `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`
  y se guardan en memoria.
- Proyectos con la llave "legacy" (HS256) necesitan `SUPABASE_JWT_SECRET`.
- Sin `SUPABASE_URL` (modo local con SQLite) no se pide sesión: todo es
  del usuario por defecto (`storage.db.default_owner_id()`).
"""

import logging
import os
import uuid

import jwt
from fastapi import HTTPException, Request

from storage.db import default_owner_id

ASYMMETRIC_ALGS = ["ES256", "RS256", "EdDSA"]
AUDIENCE = "authenticated"

logger = logging.getLogger(__name__)


class Auth:
    def __init__(self, supabase_url: str | None = None, publishable_key: str | None = None,
                 jwt_secret: str | None = None, signing_key=None):
        """`signing_key` (una llave pública) es para pruebas: evita bajar
        las llaves de Supabase."""
        self.supabase_url = supabase_url.rstrip("/") if supabase_url else None
        self.publishable_key = publishable_key
        self.jwt_secret = jwt_secret
        self.signing_key = signing_key
        self._jwks = None
        if self.supabase_url:
            self.issuer = f"{self.supabase_url}/auth/v1"
            if signing_key is None:
                self._jwks = jwt.PyJWKClient(f"{self.issuer}/.well-known/jwks.json",
                                             cache_keys=True, lifespan=3600)
        logger.info("autenticación: %s", f"Supabase ({self.supabase_url})" if self.enabled
                    else "desactivada (modo local)")

    @classmethod
    def from_env(cls) -> "Auth":
        return cls(os.environ.get("SUPABASE_URL") or None,
                   os.environ.get("SUPABASE_PUBLISHABLE_KEY")
                   or os.environ.get("SUPABASE_ANON_KEY") or None,
                   os.environ.get("SUPABASE_JWT_SECRET") or None)

    @property
    def enabled(self) -> bool:
        return self.supabase_url is not None

    def public_config(self) -> dict:
        """Lo que la página necesita para iniciar sesión (nada secreto)."""
        if not self.enabled:
            return {"auth": False}
        return {"auth": True, "supabase_url": self.supabase_url,
                "publishable_key": self.publishable_key}

    def user_id(self, token: str) -> uuid.UUID:
        """Valida el token y regresa el uuid del usuario. Lanza
        jwt.InvalidTokenError si no es válido."""
        alg = jwt.get_unverified_header(token).get("alg")
        logger.debug("validando token (alg=%s)", alg)
        if alg == "HS256":
            if not self.jwt_secret:
                raise jwt.InvalidTokenError("token HS256 pero falta SUPABASE_JWT_SECRET")
            key, algorithms = self.jwt_secret, ["HS256"]
        else:
            key = self.signing_key or self._jwks.get_signing_key_from_jwt(token).key
            algorithms = ASYMMETRIC_ALGS
        claims = jwt.decode(token, key, algorithms=algorithms, audience=AUDIENCE,
                            issuer=self.issuer, options={"require": ["exp", "sub"]})
        user = uuid.UUID(claims["sub"])
        logger.debug("token válido: usuario %s (%s)", user, claims.get("email"))
        return user

    def __call__(self, request: Request) -> uuid.UUID:
        """Dependencia de FastAPI: el usuario de la petición."""
        if not self.enabled:
            return default_owner_id()
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            logger.info("petición sin sesión a %s", request.url.path)
            raise HTTPException(401, "Inicia sesión.")
        try:
            return self.user_id(token)
        except (jwt.PyJWTError, ValueError) as exc:
            logger.warning("token rechazado en %s: %s", request.url.path, exc)
            raise HTTPException(401, "La sesión no es válida o ya venció; vuelve a "
                                     "iniciar sesión.") from exc
