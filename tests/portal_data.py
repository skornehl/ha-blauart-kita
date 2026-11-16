"""Portal data shaped like api._PortalParser's output, for the tests.

No real page is checked in: the shape follows the parser (one dict per
calendar-N tab with "hidden", "radios" and "days"), and each tab's grid
runs Monday to Sunday, so it carries preview cells of the neighbouring
months like the real portal does.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def month_tab(
    year: int,
    month: int,
    *,
    attending: bool = True,
    editable: bool = True,
    preview_radios: bool = True,
) -> dict[str, Any]:
    """One calendar tab. Weekdays have the attendance radio pair (checked
    as `attending`) and a meal, weekends neither. Preview cells of the
    neighbouring months have no meal and, with `preview_radios`, a radio
    pair of their own - the worst case for picking the right form."""
    first = date(year, month, 1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    day = first - timedelta(days=first.weekday())
    end = last + timedelta(days=6 - last.weekday())
    cal: dict[str, Any] = {
        "hidden": {"action": "change-meal-plan", "month": f"{year:04d}-{month:02d}"},
        "radios": {},
        "days": {},
    }
    while day <= end:
        iso = day.isoformat()
        own = day.month == month
        cal["hidden"][f"noteditables[{iso}]"] = "0" if editable else "1"
        if day.weekday() < 5 and (own or preview_radios):
            cal["radios"][f"meals[{iso}]"] = {"angemeldet": attending, "abgemeldet": not attending}
            cal["days"][iso] = {
                "editable": editable,
                "meals": [f"Essen am {iso}"] if own else [],
                "attending": attending,
            }
        else:
            cal["days"][iso] = {"editable": editable, "meals": [], "attending": None}
        day += timedelta(days=1)
    return cal


def weekdays(year: int, month: int) -> list[str]:
    """All Monday-Friday dates of a month."""
    day = date(year, month, 1)
    out = []
    while day.month == month:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def set_day(cal: dict[str, Any], day: str, attending: bool) -> None:
    """Set a day's radio in a tab, as a click in the portal would."""
    cal["radios"][f"meals[{day}]"] = {"angemeldet": attending, "abgemeldet": not attending}
    cal["days"][day]["attending"] = attending


def render_page(calendars: dict[str, dict[str, Any]], name: str = "Max Mustermann") -> str:
    """The tabs as portal HTML, the markup api._PortalParser reads."""
    parts = [
        '<html><body><a href="?logout=1">Abmelden</a>',
        f"<h3>Speiseplan für {name} (12345) ändern</h3>",
    ]
    for cal_id, cal in calendars.items():
        parts.append(f'<div id="{cal_id}"><form name="change-meal-plan">')
        for field, value in cal["hidden"].items():
            if not field.startswith("noteditables["):
                parts.append(f'<input type="hidden" name="{field}" value="{value}">')
        for day, info in cal["days"].items():
            flag = cal["hidden"][f"noteditables[{day}]"]
            parts.append(f'<div class="day"><input type="hidden" name="noteditables[{day}]" value="{flag}">')
            for meal in info["meals"]:
                parts.append(f'<span class="gw-meal-description">{meal} <span>Gl, W</span></span>')
            for value, checked in cal["radios"].get(f"meals[{day}]", {}).items():
                mark = " checked" if checked else ""
                parts.append(f'<input type="radio" name="meals[{day}]" value="{value}"{mark}>')
            parts.append("</div>")
        parts.append("</form></div>")
    parts.append("</body></html>")
    return "\n".join(parts)
