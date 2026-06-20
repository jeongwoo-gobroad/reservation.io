from __future__ import annotations

import base64
import hmac
import json
import time
from hashlib import sha256
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, load_settings
from .database import get_db
from .models import User


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64url(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def create_access_token(subject: str, scope: str, settings: Settings, expires_in_seconds: int = 43200) -> str:
    payload = {
        "sub": subject,
        "scope": scope,
        "iat": int(time.time()),
        "exp": int(time.time()) + expires_in_seconds,
    }
    encoded = _b64url(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(settings.secret_key.encode("utf-8"), encoded.encode("ascii"), sha256).digest()
    return f"{encoded}.{_b64url(sig)}"


def verify_access_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        encoded, signature = token.split(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from exc
    expected = _b64url(hmac.new(settings.secret_key.encode("utf-8"), encoded.encode("ascii"), sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    payload = json.loads(_unb64url(encoded).decode("utf-8"))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="expired token")
    return payload


def client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"

def bearer_token(authorization: str | None = Header(None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    return authorization.split(" ", 1)[1].strip()


def require_admin(
    token: str = Depends(bearer_token),
    settings: Settings = Depends(load_settings),
) -> dict[str, Any]:
    payload = verify_access_token(token, settings)
    if payload.get("scope") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin token required")
    return payload


def require_user(
    token: str = Depends(bearer_token),
    settings: Settings = Depends(load_settings),
    db: Session = Depends(get_db),
) -> User:
    payload = verify_access_token(token, settings)
    if payload.get("scope") != "user":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user token required")
    user = db.scalar(select(User).where(User.uuid == payload.get("sub")))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found")
    return user
