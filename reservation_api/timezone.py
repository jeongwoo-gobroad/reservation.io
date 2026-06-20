from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from .config import Settings


def local_now(settings: Settings) -> datetime:
    return datetime.now(ZoneInfo(settings.app_timezone))


def local_today(settings: Settings) -> date:
    return local_now(settings).date()

