"""Attendance logic that works on the parsed portal data alone.

No Home Assistant or aiohttp imports, so tests/test_attendance.py runs
with plain pytest. Dates are ISO strings ("YYYY-MM-DD") throughout, the
same keys the portal parser produces.

Two jobs:

* Work out which of the three calendar-N tabs a date belongs to. The tabs
  overlap at month boundaries (a tab's grid shows a few leading/trailing
  days of the neighbouring months), so the same date can show up in two
  tabs. The tab whose month it is - the month most of the tab's days fall
  in - owns it: that is the form to submit a change through, and the
  entry to trust for its state.
* Decide what to do about days the integration has never seen before
  ("Automatisch abmelden"), see plan_new_days.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

ATTENDING = "angemeldet"
NOT_ATTENDING = "abgemeldet"

# Remembered dates older than this are dropped. The portal never shows
# anything older than the start of the previous month (at most ~62 days
# back), so such a date can't come back and count as new again.
KNOWN_DAYS_KEEP = timedelta(days=120)


def calendar_month(cal: dict[str, Any]) -> str | None:
    """The month ("YYYY-MM") a calendar tab is for: the one most of its
    days fall in. A month has at least 28 days, the neighbouring months'
    preview cells at most 6 on each side, so this can't be ambiguous."""
    months = Counter(day[:7] for day in cal["days"])
    if not months:
        return None
    return months.most_common(1)[0][0]


def owned_days(calendars: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Every date with the day info from the tab that owns it. Preview
    cells of a month that has no tab of its own yet are left out."""
    days: dict[str, dict[str, Any]] = {}
    for cal in calendars.values():
        month = calendar_month(cal)
        for day, info in cal["days"].items():
            if day[:7] == month:
                days[day] = info
    return days


def _target_calendar(calendars: dict[str, dict[str, Any]], day: str) -> dict[str, Any] | None:
    """The tab to submit a change for `day` through: the owning tab if it
    has the attendance radio for that day, otherwise any tab that has it."""
    radio = f"meals[{day}]"
    candidates = [cal for cal in calendars.values() if radio in cal["radios"]]
    for cal in candidates:
        if calendar_month(cal) == day[:7]:
            return cal
    return candidates[0] if candidates else None


def build_attendance_forms(
    calendars: dict[str, dict[str, Any]], changes: dict[str, bool]
) -> list[dict[str, str]]:
    """The form submissions that apply `changes` (date -> attending).

    One form per affected month tab, each re-sending every hidden field
    and every other day's checked radio unchanged - the portal has no
    per-day endpoint, a form missing a day's radio drops that day's
    state. Raises ValueError, before anything is built, if any date has
    no attendance choice in the visible tabs or can no longer be changed.
    """
    by_calendar: dict[int, tuple[dict[str, Any], dict[str, bool]]] = {}
    for day, attending in sorted(changes.items()):
        cal = _target_calendar(calendars, day)
        if cal is None:
            raise ValueError(
                f"{day} has no attendance choice in the visible portal months (it only "
                "shows the previous/current/next calendar month)"
            )
        if not cal["days"].get(day, {}).get("editable"):
            raise ValueError(f"{day} is no longer editable in the portal")
        by_calendar.setdefault(id(cal), (cal, {}))[1][day] = attending

    forms: list[dict[str, str]] = []
    for cal, cal_changes in by_calendar.values():
        form: dict[str, str] = dict(cal["hidden"])
        for name, options in cal["radios"].items():
            day = name[len("meals[") : -1] if name.startswith("meals[") else None
            if day in cal_changes:
                form[name] = ATTENDING if cal_changes[day] else NOT_ATTENDING
                continue
            checked = next((value for value, is_checked in options.items() if is_checked), None)
            if checked is not None:
                form[name] = checked
        forms.append(form)
    return forms


@dataclass(frozen=True)
class NewDaysPlan:
    """What plan_new_days decided.

    known: the dates to remember from now on - None while there is
        nothing to remember yet (see plan_new_days).
    deregister: new days to deregister. Not in `known` yet: they are
        remembered once the portal change went through, so a failed
        attempt is retried on the next poll.
    new: every date seen for the first time, for logging.
    """

    known: frozenset[str] | None
    deregister: tuple[str, ...] = ()
    new: tuple[str, ...] = ()


def plan_new_days(
    days: dict[str, dict[str, Any]],
    known: frozenset[str] | None,
    *,
    enabled: bool,
    today: date,
) -> NewDaysPlan:
    """Decide which new days to deregister.

    `days` is owned_days() of the current fetch, `known` the dates
    remembered so far (None if nothing was ever stored). Only days with
    an attendance choice count - a weekend or holiday without one has
    nothing to deregister, and gets its chance once a choice appears.

    * Nothing remembered yet (first poll after installing or updating):
      remember what is visible now, change nothing. If the portal shows
      no day at all, keep waiting rather than remembering an empty set -
      the next real page would otherwise count as all new.
    * A date that is remembered is never touched again, whatever its
      state - that is where the user's own changes live.
    * A new date that is still editable and attending is deregistered if
      the option is on. Every other new date is just remembered: already
      deregistered, past the portal's deadline, or option off. Tracking
      goes on while the option is off, so switching it on later never
      reaches back to days that appeared in the meantime.
    """
    choosable = {day for day, info in days.items() if info.get("attending") is not None}
    if known is None:
        return NewDaysPlan(known=frozenset(choosable) if choosable else None)

    new = tuple(sorted(choosable - known))
    deregister = tuple(
        day for day in new if enabled and days[day]["editable"] and days[day]["attending"]
    )
    cutoff = (today - KNOWN_DAYS_KEEP).isoformat()
    remembered = {day for day in known | set(new) if day >= cutoff} - set(deregister)
    return NewDaysPlan(known=frozenset(remembered), deregister=deregister, new=new)
