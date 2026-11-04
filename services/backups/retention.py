"""
Which backups to keep.

A pure function of backup ids and the current time, so the rule can be read and tested in one place.
Every machine applies it to every system directory it holds; whether the directory is its own is the
only thing that changes the outcome.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from services.backups.storage import parse_backup_id


class Reason(StrEnum):
    RECENT = "recent"
    MONTHLY = "monthly"
    NEWEST = "newest"


@dataclass(frozen=True)
class Policy:
    days: int = 14
    remote_months: int = 12


def _months_ago(now: datetime, then: datetime) -> int:
    return (now.year - then.year) * 12 + now.month - then.month


def retention_reasons(backup_ids: list[str], now: datetime, own: bool, policy: Policy) -> dict[str, list[Reason]]:
    """
    Map every backup id to the reasons it is kept. An empty list means it goes.

    - Every backup younger than policy.days is kept, manual and pre-restore ones included.
    - The earliest backup of each calendar month (UTC) is kept: the 1st's whenever the machine was on
      that day, otherwise the first one after. Held systems only keep policy.remote_months of these;
      the own system keeps them until backups.clean removes them.
    - The newest own backup is kept whatever its age, so a create that keeps failing while the timer
      keeps pruning still leaves the last good one. Held systems age out completely instead.
    """
    ordered = sorted(backup_ids)
    reasons: dict[str, list[Reason]] = {backup_id: [] for backup_id in ordered}

    first_of_month: dict[tuple[int, int], str] = {}
    for backup_id in ordered:
        created_at = parse_backup_id(backup_id)
        first_of_month.setdefault((created_at.year, created_at.month), backup_id)
        if now - created_at < timedelta(days=policy.days):
            reasons[backup_id].append(Reason.RECENT)

    for backup_id in first_of_month.values():
        if own or _months_ago(now, parse_backup_id(backup_id)) < policy.remote_months:
            reasons[backup_id].append(Reason.MONTHLY)

    if own and ordered:
        reasons[ordered[-1]].append(Reason.NEWEST)
    return reasons


def backups_to_delete(backup_ids: list[str], now: datetime, own: bool, policy: Policy) -> list[str]:
    reasons = retention_reasons(backup_ids, now, own, policy)
    return [backup_id for backup_id, kept_for in reasons.items() if not kept_for]


def clean_candidates(backup_ids: list[str], cutoff: datetime, own: bool) -> list[str]:
    """Backups made before the cutoff, sparing the newest own backup whatever the cutoff says."""
    ordered = sorted(backup_ids)
    spared = ordered[-1] if own and ordered else None
    return [
        backup_id for backup_id in ordered
        if parse_backup_id(backup_id) < cutoff and backup_id != spared
    ]
