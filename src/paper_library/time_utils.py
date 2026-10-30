"""Timezone-safe helpers for API, audit, quota, and scheduling code."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from core.service_config import app_timezone


UTC = dt.UTC
LOCAL_TZ = ZoneInfo(app_timezone())
BUSINESS_TIMEZONE = str(LOCAL_TZ)

# Compatibility aliases for callers that still use the historical names.
BEIJING_TZ = LOCAL_TZ


def local_timezone() -> ZoneInfo:
    """Return the configured application timezone (APP_TIMEZONE overrides YAML)."""
    return ZoneInfo(app_timezone())


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC)


def ensure_utc(value: dt.datetime) -> dt.datetime:
    """Return an aware UTC datetime; legacy naive values are defined as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def local_now() -> dt.datetime:
    return utc_now().astimezone(local_timezone())


def to_local(value: dt.datetime) -> dt.datetime:
    return ensure_utc(value).astimezone(local_timezone())


def local_business_date(value: dt.datetime | None = None) -> dt.date:
    return to_local(value or utc_now()).date()


def local_end_of_day_utc(business_date: dt.date) -> dt.datetime:
    next_day = business_date + dt.timedelta(days=1)
    return dt.datetime.combine(next_day, dt.time.min, local_timezone()).astimezone(UTC)


def beijing_now() -> dt.datetime:
    """Compatibility alias for :func:`local_now`."""
    return local_now()


def to_beijing(value: dt.datetime) -> dt.datetime:
    """Compatibility alias for :func:`to_local`."""
    return to_local(value)


def beijing_business_date(value: dt.datetime | None = None) -> dt.date:
    """Compatibility alias for :func:`local_business_date`."""
    return local_business_date(value)


def beijing_end_of_day_utc(business_date: dt.date) -> dt.datetime:
    """Compatibility alias for :func:`local_end_of_day_utc`."""
    return local_end_of_day_utc(business_date)


def format_api_datetime(value: dt.datetime | None) -> str | None:
    """Format a stored datetime as an explicit local-time ISO-8601 value."""
    if value is None:
        return None
    return to_local(value).isoformat()


def parse_api_datetime(
    value: str,
    *,
    naive_as_local: bool = False,
    naive_as_beijing: bool | None = None,
) -> dt.datetime:
    """Parse ISO-8601 input and return aware UTC.

    New APIs should require an explicit offset. ``naive_as_local`` supports
    documented user-entered wall-clock fields such as a schedule picker.
    ``naive_as_beijing`` remains as a compatibility keyword.
    """
    if naive_as_beijing is not None:
        naive_as_local = naive_as_beijing
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        if not naive_as_local:
            raise ValueError("datetime must include a timezone offset")
        parsed = parsed.replace(tzinfo=local_timezone())
    return parsed.astimezone(UTC)
