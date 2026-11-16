"""Tests for custom_components/blauart_kita/attendance.py.

Loaded by file path so they need no Home Assistant installation (the
package __init__ imports homeassistant).
"""
from __future__ import annotations

from datetime import date
import importlib.util
from pathlib import Path
import sys

import pytest

from portal_data import month_tab, set_day, weekdays

_PATH = Path(__file__).parents[1] / "custom_components" / "blauart_kita" / "attendance.py"
_spec = importlib.util.spec_from_file_location("blauart_attendance", _PATH)
attendance = importlib.util.module_from_spec(_spec)
sys.modules["blauart_attendance"] = attendance
_spec.loader.exec_module(attendance)

TODAY = date(2026, 11, 16)


def tabs(*months: tuple[int, int], **kwargs) -> dict:
    return {f"calendar-{i}": month_tab(*m, **kwargs) for i, m in enumerate(months, 1)}


# --- which tab owns a date ---------------------------------------------------


def test_calendar_month_is_the_majority_month() -> None:
    # November 2026 starts on a Sunday: the grid opens with Oct 26-31.
    cal = month_tab(2026, 11)
    assert "2026-10-26" in cal["days"]
    assert attendance.calendar_month(cal) == "2026-11"
    assert attendance.calendar_month({"days": {}}) is None


def test_owned_days_skip_preview_cells() -> None:
    calendars = tabs((2026, 10), (2026, 11), (2026, 12))
    # Dec 1 is both a trailing cell of November's grid and a December day.
    assert "2026-12-01" in calendars["calendar-2"]["days"]
    set_day(calendars["calendar-2"], "2026-12-01", False)

    days = attendance.owned_days(calendars)
    assert days["2026-12-01"]["attending"] is True  # December's tab wins
    # January 2027 preview cells in December's grid belong to no tab yet.
    assert "2027-01-01" in calendars["calendar-3"]["days"]
    assert not any(day.startswith("2027-") for day in days)
    assert not any(day.startswith("2026-09") for day in days)
    assert min(days) == "2026-10-01" and max(days) == "2026-12-31"


# --- the forms a change is submitted with -------------------------------------


def test_forms_keep_every_other_day() -> None:
    calendars = tabs((2026, 10), (2026, 11), (2026, 12))
    set_day(calendars["calendar-2"], "2026-11-17", False)

    [form] = attendance.build_attendance_forms(calendars, {"2026-11-18": False})
    nov = calendars["calendar-2"]
    assert form["month"] == "2026-11"
    assert form["noteditables[2026-11-18]"] == "0"
    assert form["meals[2026-11-18]"] == "abgemeldet"
    assert form["meals[2026-11-17]"] == "abgemeldet"  # untouched, still sent
    assert form["meals[2026-11-19]"] == "angemeldet"
    assert {k for k in form if k.startswith("meals[")} == set(nov["radios"])
    # The input is left as it was.
    assert nov["days"]["2026-11-18"]["attending"] is True


def test_forms_leave_out_days_without_a_checked_choice() -> None:
    # Neither radio checked: the portal's own default applies, so the
    # form must not invent a value for it.
    calendars = tabs((2026, 11))
    calendars["calendar-1"]["radios"]["meals[2026-11-19]"] = {"angemeldet": False, "abgemeldet": False}

    [form] = attendance.build_attendance_forms(calendars, {"2026-11-18": False})
    assert "meals[2026-11-19]" not in form
    assert form["meals[2026-11-20]"] == "angemeldet"


def test_forms_one_per_month() -> None:
    calendars = tabs((2026, 10), (2026, 11), (2026, 12))
    changes = {day: False for day in weekdays(2026, 12)} | {"2026-11-30": True}

    forms = attendance.build_attendance_forms(calendars, changes)
    assert sorted(form["month"] for form in forms) == ["2026-11", "2026-12"]
    dec = next(form for form in forms if form["month"] == "2026-12")
    assert all(dec[f"meals[{day}]"] == "abgemeldet" for day in weekdays(2026, 12))
    # Dec 1-4 are November preview cells too - they go with December's form.
    nov = next(form for form in forms if form["month"] == "2026-11")
    assert nov["meals[2026-12-01]"] == "angemeldet"
    assert nov["meals[2026-11-30]"] == "angemeldet"


def test_forms_fall_back_to_a_preview_cell() -> None:
    # Only November is shown: Dec 1 has no tab of its own, just November's
    # preview cell - the only place it can be changed.
    calendars = tabs((2026, 11))
    [form] = attendance.build_attendance_forms(calendars, {"2026-12-01": False})
    assert form["month"] == "2026-11"
    assert form["meals[2026-12-01]"] == "abgemeldet"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"2027-03-02": False}, "no attendance choice"),
        ({"2026-11-21": False}, "no attendance choice"),  # a Saturday
        ({"2026-12-01": False, "2026-12-02": False, "2026-11-02": False}, "no longer editable"),
    ],
)
def test_forms_refuse_before_building_anything(changes: dict, message: str) -> None:
    calendars = tabs((2026, 10), (2026, 11), (2026, 12))
    calendars["calendar-2"]["days"]["2026-11-02"]["editable"] = False
    with pytest.raises(ValueError, match=message):
        attendance.build_attendance_forms(calendars, changes)


# --- which new days get deregistered ------------------------------------------


def owned(*months: tuple[int, int], **kwargs) -> dict:
    return attendance.owned_days(tabs(*months, **kwargs))


def test_first_poll_only_remembers() -> None:
    days = owned((2026, 10), (2026, 11), (2026, 12))
    plan = attendance.plan_new_days(days, None, enabled=True, today=TODAY)
    assert plan.deregister == () and plan.new == ()
    assert plan.known == frozenset(weekdays(2026, 10) + weekdays(2026, 11) + weekdays(2026, 12))


def test_first_poll_without_days_waits() -> None:
    plan = attendance.plan_new_days({}, None, enabled=True, today=TODAY)
    assert plan.known is None
    # A page with only weekends/holidays (no attendance choice) neither.
    no_choice = {"2026-11-21": {"editable": True, "meals": [], "attending": None}}
    assert attendance.plan_new_days(no_choice, None, enabled=True, today=TODAY).known is None


def test_new_month_is_deregistered() -> None:
    known = attendance.plan_new_days(
        owned((2026, 10), (2026, 11), (2026, 12)), None, enabled=True, today=TODAY
    ).known
    # November and December are known and still attending - left alone.
    days = owned((2026, 11), (2026, 12), (2027, 1))
    plan = attendance.plan_new_days(days, known, enabled=True, today=TODAY)
    assert plan.new == tuple(weekdays(2027, 1))
    assert plan.deregister == tuple(weekdays(2027, 1))
    assert not plan.known & set(plan.deregister)  # remembered once done
    assert set(weekdays(2026, 12)) <= plan.known


def test_new_days_that_need_nothing_are_remembered() -> None:
    known = frozenset(weekdays(2026, 11))
    calendars = tabs((2026, 11), (2026, 12))
    dec = calendars["calendar-2"]
    set_day(dec, "2026-12-01", False)  # already deregistered
    dec["days"]["2026-12-02"]["editable"] = False  # past the deadline
    days = attendance.owned_days(calendars)

    plan = attendance.plan_new_days(days, known, enabled=True, today=TODAY)
    assert "2026-12-01" not in plan.deregister and "2026-12-01" in plan.known
    assert "2026-12-02" not in plan.deregister and "2026-12-02" in plan.known
    assert "2026-12-03" in plan.deregister and "2026-12-03" not in plan.known


def test_option_off_only_remembers() -> None:
    known = frozenset(weekdays(2026, 11))
    days = owned((2026, 11), (2026, 12))
    plan = attendance.plan_new_days(days, known, enabled=False, today=TODAY)
    assert plan.new == tuple(weekdays(2026, 12))
    assert plan.deregister == ()
    assert plan.known == known | set(weekdays(2026, 12))
    # Switching the option on afterwards finds nothing new.
    again = attendance.plan_new_days(days, plan.known, enabled=True, today=TODAY)
    assert again.deregister == () and again.new == ()


def test_known_days_are_never_touched() -> None:
    days = owned((2026, 11), (2026, 12))
    known = frozenset(days)
    plan = attendance.plan_new_days(days, known, enabled=True, today=TODAY)
    assert plan.deregister == () and plan.new == ()


def test_day_gaining_a_choice_counts_as_new() -> None:
    calendars = tabs((2026, 12))
    dec = calendars["calendar-1"]
    del dec["radios"]["meals[2026-12-24]"]
    dec["days"]["2026-12-24"]["attending"] = None
    first = attendance.plan_new_days(attendance.owned_days(calendars), None, enabled=True, today=TODAY)
    assert "2026-12-24" not in first.known

    plan = attendance.plan_new_days(owned((2026, 12)), first.known, enabled=True, today=TODAY)
    assert plan.deregister == ("2026-12-24",)


def test_old_dates_are_forgotten() -> None:
    known = frozenset(["2026-01-05", "2026-07-20", "2026-11-02"])
    plan = attendance.plan_new_days({}, known, enabled=True, today=TODAY)
    # Cutoff is 120 days back: 2026-07-19.
    assert plan.known == frozenset(["2026-07-20", "2026-11-02"])
