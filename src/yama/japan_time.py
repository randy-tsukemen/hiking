"""Japan wall-clock time for booking schedules, independent of the host timezone.

BookingWindow and HutDay store naive Japanese wall times. Keep that convention
at this boundary; explicit UTC+09:00 avoids a Windows tzdata dependency.
"""

from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9), "JST")


def japan_now() -> datetime:
    """Return current Japanese wall time without tzinfo, matching booking data."""
    return datetime.now(JST).replace(tzinfo=None)
