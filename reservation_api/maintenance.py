from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from . import models
from .config import load_settings
from .database import Base, SessionLocal, engine


RESERVATION_REQUEST_RETENTION_HOURS = 24


@dataclass(frozen=True)
class CleanupResult:
    deleted_reservation_requests: int
    cutoff: datetime
    configured_gmt9_hour: int


def cleanup_reservation_requests(
    db: Session,
    *,
    now: datetime | None = None,
    retention_hours: int = RESERVATION_REQUEST_RETENTION_HOURS,
) -> CleanupResult:
    settings = load_settings()
    reference_now = now or datetime.now(timezone.utc)
    cutoff = reference_now - timedelta(hours=retention_hours)
    result = db.execute(delete(models.ReservationRequest).where(models.ReservationRequest.created_at < cutoff))
    db.commit()
    return CleanupResult(
        deleted_reservation_requests=int(result.rowcount or 0),
        cutoff=cutoff,
        configured_gmt9_hour=settings.cleanup_gmt9_hour,
    )


def run_cleanup_reservation_requests() -> CleanupResult:
    settings = load_settings()
    if settings.is_dev_mode and settings.database_url.startswith("sqlite"):
        Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        return cleanup_reservation_requests(db)


def main() -> None:
    parser = argparse.ArgumentParser(description="reservation.io maintenance commands")
    parser.add_argument("command", choices=["cleanup-reservation-requests"])
    args = parser.parse_args()

    if args.command == "cleanup-reservation-requests":
        result = run_cleanup_reservation_requests()
        print(
            "cleanup_reservation_requests "
            f"deleted={result.deleted_reservation_requests} "
            f"cutoff={result.cutoff.isoformat()} "
            f"configured_gmt9_hour={result.configured_gmt9_hour}"
        )


if __name__ == "__main__":
    main()
