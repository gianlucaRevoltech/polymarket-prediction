"""Utility temporali UTC per persistenza, API e compatibilità legacy."""
from datetime import datetime, timezone
from typing import Optional, Union


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_utc(value: Union[str, int, float, datetime, None]) -> Optional[datetime]:
    """Normalizza timestamp ISO/epoch; i valori legacy naïve sono UTC."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, (int, float)):
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            raw = str(value).strip()
            if raw.isdigit():
                parsed = datetime.fromtimestamp(float(raw), tz=timezone.utc)
            else:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def utc_iso(value: Union[str, int, float, datetime, None]) -> Optional[str]:
    parsed = parse_utc(value)
    return parsed.isoformat() if parsed else None


def age_seconds(value, *, now: Optional[datetime] = None) -> Optional[float]:
    parsed = parse_utc(value)
    if parsed is None:
        return None
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0.0, (current.astimezone(timezone.utc) - parsed).total_seconds())
