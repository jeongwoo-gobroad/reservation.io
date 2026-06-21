from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Protocol

from .config import Settings
from .otp import current_counter, manual_code


@dataclass(frozen=True)
class ManualCodeReservation:
    code: str
    nonce: int
    counter: int
    expires_at: datetime


@dataclass(frozen=True)
class ManualCodeResolution:
    code: str
    user_uuid: str
    reservation_uuid: str
    counter: int
    nonce: int
    expires_at: datetime


class ManualCodeCollisionError(RuntimeError):
    pass


class ManualCodeRegistry(Protocol):
    def reserve(self, user_uuid: str, reservation_uuid: str, settings: Settings) -> ManualCodeReservation:
        ...

    def resolve(self, code: str) -> ManualCodeResolution | None:
        ...

    def delete_user(self, user_uuid: str) -> None:
        ...


def counter_expires_at(counter: int, settings: Settings) -> datetime:
    expires_epoch = (counter + settings.otp_delta + 1) * settings.otp_timeout_second
    return datetime.fromtimestamp(expires_epoch, tz=timezone.utc)


class DisabledManualCodeRegistry:
    def reserve(self, user_uuid: str, reservation_uuid: str, settings: Settings) -> ManualCodeReservation:
        counter = current_counter(settings)
        return ManualCodeReservation(
            code=manual_code(user_uuid, reservation_uuid, settings, counter=counter),
            nonce=0,
            counter=counter,
            expires_at=counter_expires_at(counter, settings),
        )

    def resolve(self, code: str) -> ManualCodeResolution | None:
        return None

    def delete_user(self, user_uuid: str) -> None:
        return None


class MemoryManualCodeRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._codes: dict[str, dict] = {}

    def reserve(self, user_uuid: str, reservation_uuid: str, settings: Settings) -> ManualCodeReservation:
        now = datetime.now(timezone.utc)
        counter = current_counter(settings)
        expires_at = counter_expires_at(counter, settings)
        with self._lock:
            self._delete_expired(now)
            for nonce in range(settings.otp_manual_code_max_attempts):
                code = manual_code(user_uuid, reservation_uuid, settings, counter=counter, nonce=nonce)
                existing = self._codes.get(code)
                if existing and existing["expires_at"] > now and not self._same_reservation(existing, user_uuid, reservation_uuid, counter, nonce):
                    continue
                self._codes[code] = self._payload(code, user_uuid, reservation_uuid, counter, nonce, now, expires_at)
                return ManualCodeReservation(code=code, nonce=nonce, counter=counter, expires_at=expires_at)
        raise ManualCodeCollisionError("manual code collision could not be resolved")

    def resolve(self, code: str) -> ManualCodeResolution | None:
        normalized = code.strip().lower()
        now = datetime.now(timezone.utc)
        with self._lock:
            self._delete_expired(now)
            payload = self._codes.get(normalized)
            if not payload or payload["expires_at"] <= now:
                return None
            return ManualCodeResolution(
                code=normalized,
                user_uuid=payload["user_uuid"],
                reservation_uuid=payload["reservation_uuid"],
                counter=payload["counter"],
                nonce=payload["nonce"],
                expires_at=payload["expires_at"],
            )

    def delete_user(self, user_uuid: str) -> None:
        with self._lock:
            matching_codes = [code for code, payload in self._codes.items() if payload.get("user_uuid") == user_uuid]
            for code in matching_codes:
                self._codes.pop(code, None)

    def _delete_expired(self, now: datetime) -> None:
        expired = [code for code, payload in self._codes.items() if payload["expires_at"] <= now]
        for code in expired:
            self._codes.pop(code, None)

    @staticmethod
    def _same_reservation(payload: dict, user_uuid: str, reservation_uuid: str, counter: int, nonce: int) -> bool:
        return (
            payload["user_uuid"] == user_uuid
            and payload["reservation_uuid"] == reservation_uuid
            and payload["counter"] == counter
            and payload["nonce"] == nonce
        )

    @staticmethod
    def _payload(
        code: str,
        user_uuid: str,
        reservation_uuid: str,
        counter: int,
        nonce: int,
        issued_at: datetime,
        expires_at: datetime,
    ) -> dict:
        return {
            "code": code,
            "user_uuid": user_uuid,
            "reservation_uuid": reservation_uuid,
            "counter": counter,
            "nonce": nonce,
            "issued_at": issued_at,
            "expires_at": expires_at,
        }


class FirestoreManualCodeRegistry:
    def __init__(self, settings: Settings) -> None:
        from google.cloud import firestore

        project = settings.firestore_project_id or None
        database = settings.firestore_database or "(default)"
        self._firestore = firestore
        self._client = firestore.Client(project=project, database=database)
        self._collection = self._client.collection(settings.otp_manual_code_collection)

    def reserve(self, user_uuid: str, reservation_uuid: str, settings: Settings) -> ManualCodeReservation:
        now = datetime.now(timezone.utc)
        counter = current_counter(settings)
        expires_at = counter_expires_at(counter, settings)
        for nonce in range(settings.otp_manual_code_max_attempts):
            code = manual_code(user_uuid, reservation_uuid, settings, counter=counter, nonce=nonce)
            doc_ref = self._collection.document(code)
            transaction = self._client.transaction()

            @self._firestore.transactional
            def reserve_in_transaction(txn):
                snapshot = doc_ref.get(transaction=txn)
                if snapshot.exists:
                    data = snapshot.to_dict() or {}
                    existing_expires_at = data.get("expires_at")
                    active = existing_expires_at and existing_expires_at > now
                    same_reservation = (
                        data.get("user_uuid") == user_uuid
                        and data.get("reservation_uuid") == reservation_uuid
                        and data.get("counter") == counter
                        and data.get("nonce") == nonce
                    )
                    if active and not same_reservation:
                        return False
                txn.set(
                    doc_ref,
                    {
                        "code": code,
                        "user_uuid": user_uuid,
                        "reservation_uuid": reservation_uuid,
                        "counter": counter,
                        "nonce": nonce,
                        "issued_at": now,
                        "expires_at": expires_at,
                    },
                )
                return True

            if reserve_in_transaction(transaction):
                return ManualCodeReservation(code=code, nonce=nonce, counter=counter, expires_at=expires_at)
        raise ManualCodeCollisionError("manual code collision could not be resolved")

    def resolve(self, code: str) -> ManualCodeResolution | None:
        normalized = code.strip().lower()
        snapshot = self._collection.document(normalized).get()
        if not snapshot.exists:
            return None
        data = snapshot.to_dict() or {}
        expires_at = data.get("expires_at")
        if not expires_at or expires_at <= datetime.now(timezone.utc):
            return None
        return ManualCodeResolution(
            code=normalized,
            user_uuid=data["user_uuid"],
            reservation_uuid=data["reservation_uuid"],
            counter=int(data["counter"]),
            nonce=int(data.get("nonce", 0)),
            expires_at=expires_at,
        )

    def delete_user(self, user_uuid: str) -> None:
        docs = self._collection.where("user_uuid", "==", user_uuid).stream()
        batch = self._client.batch()
        pending = 0
        for snapshot in docs:
            batch.delete(snapshot.reference)
            pending += 1
            if pending >= 400:
                batch.commit()
                batch = self._client.batch()
                pending = 0
        if pending:
            batch.commit()


def get_manual_code_registry(settings: Settings) -> ManualCodeRegistry:
    backend = settings.otp_manual_code_registry_backend.strip().lower()
    if backend == "firestore":
        return FirestoreManualCodeRegistry(settings)
    if backend == "memory":
        return MemoryManualCodeRegistry()
    return DisabledManualCodeRegistry()
