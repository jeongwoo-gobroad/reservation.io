from __future__ import annotations

import hashlib
import hmac
import time

from .config import Settings


class UnsupportedHashAlgorithm(ValueError):
    pass


def current_counter(settings: Settings, now: int | None = None) -> int:
    epoch = int(time.time() if now is None else now)
    return epoch // settings.otp_timeout_second


def remaining_seconds(settings: Settings, now: int | None = None) -> int:
    epoch = int(time.time() if now is None else now)
    return settings.otp_timeout_second - (epoch % settings.otp_timeout_second)


def hash_token(material: str, algorithm: str, secret: str) -> str:
    algorithm = algorithm.strip().lower()
    if algorithm not in hashlib.algorithms_available:
        raise UnsupportedHashAlgorithm(f"unsupported hash algorithm: {algorithm}")
    return hmac.new(secret.encode("utf-8"), material.encode("utf-8"), algorithm).hexdigest()


def token_material(user_uuid: str, reservation_uuid: str, counter: int) -> str:
    return f"{user_uuid}:{reservation_uuid}:{counter}"


def qr_hash(user_uuid: str, reservation_uuid: str, settings: Settings, counter: int | None = None) -> str:
    counter = current_counter(settings) if counter is None else counter
    return hash_token(token_material(user_uuid, reservation_uuid, counter), settings.otp_qr_hash_algorithm, settings.otp_secret_key)


def manual_code(user_uuid: str, reservation_uuid: str, settings: Settings, counter: int | None = None) -> str:
    counter = current_counter(settings) if counter is None else counter
    raw = hash_token(token_material(user_uuid, reservation_uuid, counter), settings.otp_manual_hash_algorithm, settings.otp_secret_key)
    return raw[: settings.otp_manual_code_length]


def allowed_counters(settings: Settings, now: int | None = None) -> range:
    center = current_counter(settings, now)
    return range(center - settings.otp_delta, center + settings.otp_delta + 1)


def verify_token(token: str, user_uuid: str, reservation_uuid: str, settings: Settings, token_type: str = "auto") -> bool:
    normalized = token.strip().lower()
    for counter in allowed_counters(settings):
        qr_candidate = qr_hash(user_uuid, reservation_uuid, settings, counter)
        manual_candidate = manual_code(user_uuid, reservation_uuid, settings, counter)
        if token_type in {"auto", "qr"} and hmac.compare_digest(normalized, qr_candidate.lower()):
            return True
        if token_type in {"auto", "manual"} and hmac.compare_digest(normalized, manual_candidate.lower()):
            return True
    return False

