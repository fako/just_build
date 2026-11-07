"""
What this machine knows about every system's backups, read from local files only.

No SSH and no database: status answers from what is on disk, so it works on a machine whose stack is
down and never waits on a remote that is unreachable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from services.backups.remotes import Remote
from services.backups.retention import Policy, Reason, retention_reasons
from services.backups.storage import (
    CORRUPT_SUFFIX,
    Backup,
    directory_size,
    list_backups,
    list_suffixed,
    system_names,
)


@dataclass
class SystemStatus:
    system: str
    source: str
    backups: list[Backup]
    monthly: int
    size: int
    notes: list[str] = field(default_factory=list)

    @property
    def latest(self) -> Backup | None:
        return self.backups[-1] if self.backups else None


def format_age(age: timedelta) -> str:
    hours = age.total_seconds() / 3600
    if hours < 1:
        return f"{max(int(age.total_seconds() // 60), 0)}m"
    if hours < 48:
        return f"{int(hours)}h"
    return f"{int(hours // 24)}d"


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def format_time(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def system_order(root: Path, own_system: str, remotes: dict[str, Remote]) -> list[tuple[str, str]]:
    """Own system first, then the registered systems by name, then whatever else the root holds."""
    order = [(own_system, "own"), *((name, "pull") for name in sorted(remotes))]
    listed = {name for name, _ in order}
    order.extend((name, "unregistered") for name in system_names(root) if name not in listed)
    return order


def system_status(root: Path, system: str, source: str, now: datetime, policy: Policy,
                  stale_after: timedelta) -> SystemStatus:
    backups = list_backups(root, system)
    reasons = retention_reasons([backup.id for backup in backups], now, source == "own", policy)
    status = SystemStatus(
        system=system,
        source=source,
        backups=backups,
        monthly=sum(Reason.MONTHLY in kept_for for kept_for in reasons.values()),
        size=sum(directory_size(backup.path) for backup in backups),
    )
    if status.latest is None:
        status.notes.append("no backups yet")
    elif now - status.latest.created_at > stale_after:
        status.notes.append("stale")
    corrupt = list_suffixed(root, system, CORRUPT_SUFFIX)
    if corrupt:
        status.notes.append(f"{len(corrupt)} corrupt")
    return status


def render_table(header: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(row[index]) for row in [header, *rows]) for index in range(len(header))]
    lines = ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in [header, *rows]]
    return "\n".join(lines)


def render_overview(systems: list[SystemStatus], now: datetime) -> str:
    rows = []
    for status in systems:
        latest = status.latest
        rows.append([
            status.system,
            status.source,
            format_time(latest.created_at) if latest else "-",
            format_age(now - latest.created_at) if latest else "-",
            str(len(status.backups)),
            str(status.monthly),
            format_size(status.size),
            ", ".join(status.notes),
        ])
    return render_table(["SYSTEM", "SOURCE", "LATEST", "AGE", "BACKUPS", "MONTHLY", "SIZE", "NOTE"], rows)


def render_system(status: SystemStatus, now: datetime, policy: Policy) -> str:
    reasons = retention_reasons([backup.id for backup in status.backups], now, status.source == "own", policy)
    rows = []
    for backup in reversed(status.backups):
        label = backup.manifest().get("label") or ""
        kept_for = ", ".join(reasons[backup.id]) or "deleted at next prune"
        rows.append([backup.id, format_age(now - backup.created_at), label, format_size(directory_size(backup.path)),
                     kept_for])
    header = f"{status.system} ({status.source})"
    if not rows:
        return f"{header}: no backups"
    return header + "\n\n" + render_table(["BACKUP", "AGE", "LABEL", "SIZE", "KEPT"], rows)
