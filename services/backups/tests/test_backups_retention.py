from datetime import datetime, timedelta, timezone

import pytest

from services.backups.retention import Policy, Reason, backups_to_delete, clean_candidates, retention_reasons
from services.backups.storage import new_backup_id


NOW = datetime(2026, 11, 20, 3, 0, tzinfo=timezone.utc)
POLICY = Policy(days=14, remote_months=12)


def daily(start: datetime, days: int) -> list[str]:
    return [new_backup_id(start + timedelta(days=offset)) for offset in range(days)]


def test_every_backup_from_the_last_14_days_is_kept():
    backup_ids = daily(NOW - timedelta(days=13), 14)
    # A manual backup in between counts as much as the daily ones.
    backup_ids.append(new_backup_id(NOW - timedelta(days=3, hours=5)))

    assert backups_to_delete(backup_ids, NOW, own=True, policy=POLICY) == []


def test_the_window_ends_at_14_days():
    inside = new_backup_id(NOW - timedelta(days=13, hours=23))
    outside = new_backup_id(NOW - timedelta(days=14))

    reasons = retention_reasons([inside, outside], NOW, own=False, policy=POLICY)

    assert Reason.RECENT in reasons[inside]
    assert Reason.RECENT not in reasons[outside]


def test_the_first_of_the_month_is_kept_and_the_rest_of_the_month_goes():
    october = daily(datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc), 31)

    doomed = backups_to_delete(october, NOW, own=False, policy=POLICY)

    assert october[0] == "20261001T030000Z"
    assert october[0] not in doomed
    # Everything after the 1st and older than 14 days goes.
    assert doomed == [backup_id for backup_id in october[1:] if backup_id < new_backup_id(NOW - timedelta(days=14))]


def test_a_month_without_a_backup_on_the_1st_keeps_its_earliest():
    backup_ids = ["20260903T030000Z", "20260904T030000Z", "20260910T030000Z"]

    reasons = retention_reasons(backup_ids, NOW, own=True, policy=POLICY)

    assert reasons["20260903T030000Z"] == [Reason.MONTHLY]
    assert reasons["20260904T030000Z"] == []


def test_months_are_calendar_months_in_utc():
    # 23:30 on the 31st in Amsterdam is already the 1st in some zones, but ids are UTC and so is the month.
    backup_ids = ["20260831T233000Z", "20260901T000500Z"]

    reasons = retention_reasons(backup_ids, NOW, own=False, policy=POLICY)

    assert reasons["20260831T233000Z"] == [Reason.MONTHLY]
    assert reasons["20260901T000500Z"] == [Reason.MONTHLY]


def test_held_systems_keep_a_year_of_monthlies_and_the_own_system_keeps_them_all():
    monthlies = [f"{year}{month:02d}01T030000Z" for year in (2024, 2025, 2026) for month in range(1, 13)]
    monthlies = [backup_id for backup_id in monthlies if backup_id < "20261120"]

    held = backups_to_delete(monthlies, NOW, own=False, policy=POLICY)
    own = backups_to_delete(monthlies, NOW, own=True, policy=POLICY)

    assert own == []
    # December 2025 up to November 2026 is a calendar year's worth.
    assert held[-1] == "20251101T030000Z"
    assert "20251201T030000Z" not in held


def test_the_newest_own_backup_survives_a_create_that_keeps_failing():
    backup_ids = ["20260902T030000Z", "20260903T030000Z", "20260904T030000Z"]

    reasons = retention_reasons(backup_ids, NOW, own=True, policy=POLICY)

    assert reasons["20260904T030000Z"] == [Reason.NEWEST]
    assert reasons["20260903T030000Z"] == []


def test_a_silent_held_system_ages_out_apart_from_its_monthlies():
    backup_ids = ["20260902T030000Z", "20260903T030000Z", "20260904T030000Z"]

    assert backups_to_delete(backup_ids, NOW, own=False, policy=POLICY) == ["20260903T030000Z", "20260904T030000Z"]


def test_clean_deletes_before_the_cutoff_and_spares_the_newest_own_backup():
    backup_ids = ["20240101T030000Z", "20250101T030000Z", "20250601T030000Z"]
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)

    assert clean_candidates(backup_ids, cutoff, own=True) == ["20240101T030000Z", "20250101T030000Z"]
    assert clean_candidates(backup_ids, cutoff, own=False) == backup_ids


@pytest.mark.parametrize("cutoff, expected", [
    (datetime(2025, 1, 1, tzinfo=timezone.utc), ["20240101T030000Z"]),
    (datetime(2025, 1, 1, 3, 0, 1, tzinfo=timezone.utc), ["20240101T030000Z", "20250101T030000Z"]),
])
def test_clean_compares_against_the_exact_cutoff(cutoff, expected):
    backup_ids = ["20240101T030000Z", "20250101T030000Z", "20260101T030000Z"]

    assert clean_candidates(backup_ids, cutoff, own=True) == expected
