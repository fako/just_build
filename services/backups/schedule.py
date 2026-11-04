"""
When the hourly timer should make a backup.

A backup day starts at backups.daily_at, local time, 03:00 by default. The timer checks every hour and
after boot, and a check makes a backup only when the own system has none since the most recent start
of a day. A machine that is on at 03:00 therefore backs up at 03:00; one that is not backs up at its
first check after that, and never more than once a day.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from services.backups.storage import BackupError, parse_backup_id


def parse_daily_at(value: str) -> time:
    try:
        return datetime.strptime(str(value), "%H:%M").time()
    except ValueError as exc:
        raise BackupError(f"backups.daily_at should be a time like 03:00, not '{value}'.") from exc


def day_start(now: datetime, daily_at: time) -> datetime:
    """
    The most recent start of a backup day, in UTC.

    Worked out on the local wall clock, so it stays at 03:00 across daylight saving changes, and only
    then converted to compare with backup ids, which are UTC.
    """
    local_now = now.astimezone().replace(tzinfo=None)
    start = local_now.replace(hour=daily_at.hour, minute=daily_at.minute, second=0, microsecond=0)
    if start > local_now:
        start -= timedelta(days=1)
    return start.astimezone(timezone.utc)


def backup_due(backup_ids: list[str], now: datetime, daily_at: time) -> bool:
    if not backup_ids:
        return True
    return parse_backup_id(max(backup_ids)) < day_start(now, daily_at)
