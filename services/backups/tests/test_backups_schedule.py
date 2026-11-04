from datetime import datetime, time, timezone
import os
import time as time_module

import pytest

from services.backups.schedule import backup_due, day_start, parse_daily_at
from services.backups.storage import BackupError


THREE = time(3, 0)


@pytest.fixture(autouse=True)
def amsterdam():
    """Pin the local timezone, since a backup day starts on the local wall clock."""
    original = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Amsterdam"
    time_module.tzset()
    yield
    if original is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = original
    time_module.tzset()


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def test_the_day_starts_at_03_00_local_time():
    # 10:00 UTC on a winter day is 11:00 in Amsterdam, after today's 03:00 local, which is 02:00 UTC.
    assert day_start(utc("2026-11-20T10:00:00"), THREE) == utc("2026-11-20T02:00:00")


def test_before_03_00_the_day_started_yesterday():
    # 01:30 UTC is 02:30 in Amsterdam, before 03:00, so the day began at yesterday's 03:00 local.
    assert day_start(utc("2026-11-20T01:30:00"), THREE) == utc("2026-11-19T02:00:00")


def test_summer_time_keeps_the_wall_clock():
    assert day_start(utc("2026-07-01T10:00:00"), THREE) == utc("2026-07-01T01:00:00")


def test_an_always_on_machine_backs_up_at_03_00_and_not_again_that_day():
    made_at_three = ["20261120T020000Z"]

    assert backup_due([], utc("2026-11-20T02:00:00"), THREE)
    assert backup_due(["20261119T020000Z"], utc("2026-11-20T02:00:00"), THREE)
    assert not backup_due(made_at_three, utc("2026-11-20T12:00:00"), THREE)
    assert not backup_due(made_at_three, utc("2026-11-21T01:00:00"), THREE)
    assert backup_due(made_at_three, utc("2026-11-21T02:00:00"), THREE)


def test_a_machine_switched_on_late_backs_up_at_its_first_check():
    assert backup_due(["20261119T140000Z"], utc("2026-11-20T08:10:00"), THREE)


def test_a_machine_on_before_03_00_waits_for_the_new_day():
    # Yesterday's backup was made after yesterday's 03:00, so at 01:00 local nothing is due yet.
    assert not backup_due(["20261119T140000Z"], utc("2026-11-20T00:00:00"), THREE)


@pytest.mark.parametrize("value", ["3am", "25:00", "", "03:00:00"])
def test_daily_at_must_be_a_time(value):
    with pytest.raises(BackupError):
        parse_daily_at(value)


def test_daily_at_parses():
    assert parse_daily_at("04:30") == time(4, 30)
