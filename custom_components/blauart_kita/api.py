"""Minimal async client for the BlauArt Kita-/Schulverpflegung customer
portal (https://www.blauart.de/kita-schulverpflegung/kundenportal/).

There's no public API - this is a plain WordPress-hosted, session-cookie
authenticated HTML form. The portal always shows exactly three tabs
(<div id="calendar-1/2/3">), each one calendar month, each containing one
<form name="change-meal-plan"> with a day-per-cell grid: a hidden
`noteditables[YYYY-MM-DD]` field (whether that day can still be changed),
a `meals[YYYY-MM-DD]` radio pair (`angemeldet`/`abgemeldet` - attending or
not), and (if a meal is actually served that day) one or more
`gw-meal-description` spans describing it. Reverse-engineered by fetching
the logged-in page and inspecting its exact markup - see the parser below
for the concrete structure being relied on.

Changing a day's attendance means re-submitting *that whole month's* form
with every day's current radio value preserved except the one being
changed - the portal has no per-day endpoint, submitting a single field
would silently drop every other day's state back to whatever the (empty)
default is. See BlauArtClient.async_set_attendance.
"""
from __future__ import annotations

import logging
from html.parser import HTMLParser
from typing import Any

from aiohttp import ClientSession, ClientTimeout

_LOGGER = logging.getLogger(__name__)

PORTAL_URL = "https://www.blauart.de/kita-schulverpflegung/kundenportal/"
TIMEOUT = ClientTimeout(total=20)
# The portal's WAF returns a bare 403 for aiohttp's default "Python/x.y
# aiohttp/x.y" User-Agent - a normal browser UA gets through fine, so we
# send one explicitly on every request instead of relying on HA's shared
# ClientSession's (integration-agnostic, so aiohttp-default) headers.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
}


class BlauArtApiError(Exception):
    """Raised on any request-level failure (HTTP error, unexpected page)."""


class BlauArtAuthError(BlauArtApiError):
    """Raised specifically when login fails (wrong customer number/password)."""


class _PortalParser(HTMLParser):
    """Parses the logged-in portal page into structured data: the
    customer's name, and - per <div id="calendar-N"> month block - every
    day's editability, current attendance, and meal description(s).

    Deliberately keyed off the hidden `noteditables[YYYY-MM-DD]` field's
    *name* for the current date, rather than parsing the human-readable
    "28.09.2026" date-of-day span text - the field name already carries
    an unambiguous ISO date, no month/day reformatting needed.
    """

    def __init__(self) -> None:
        super().__init__()
        self.customer_name: str | None = None
        self.calendars: dict[str, dict[str, Any]] = {}
        self._current_calendar: str | None = None
        self._calendar_depth = 0
        self._current_date: str | None = None
        self._meal_desc_depth = 0
        self._meal_text: list[str] = []
        self._in_h3 = False
        self._h3_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = dict(attrs)
        if tag == "h3":
            self._in_h3 = True
            self._h3_text = ""
            return
        if tag == "div":
            div_id = d.get("id") or ""
            if div_id.startswith("calendar-"):
                self._current_calendar = div_id
                self.calendars.setdefault(div_id, {"hidden": {}, "radios": {}, "days": {}})
                self._calendar_depth = 1
                return
            if self._current_calendar:
                self._calendar_depth += 1
            return
        if not self._current_calendar:
            return
        cal = self.calendars[self._current_calendar]
        if tag == "input":
            name = d.get("name")
            if not name:
                return
            itype = d.get("type") or "text"
            value = d.get("value") or ""
            if itype == "hidden":
                cal["hidden"][name] = value
                if name.startswith("noteditables["):
                    date = name[len("noteditables[") : -1]
                    self._current_date = date
                    cal["days"].setdefault(date, {"editable": value == "0", "meals": []})
            elif itype == "radio":
                cal["radios"].setdefault(name, {})[value] = "checked" in d
        elif tag == "span":
            classes = (d.get("class") or "").split()
            if "gw-meal-description" in classes and self._meal_desc_depth == 0:
                self._meal_desc_depth = 1
                self._meal_text = []
            elif self._meal_desc_depth > 0:
                # A nested span (e.g. an allergen marker like "Gl, W") -
                # track depth so its closing tag doesn't end the outer
                # description early.
                self._meal_desc_depth += 1

    def handle_data(self, data: str) -> None:
        if self._in_h3:
            self._h3_text += data
        if self._meal_desc_depth > 0:
            self._meal_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3" and self._in_h3:
            self._in_h3 = False
            # "Speiseplan für Max Mustermann (12345) ändern"
            text = " ".join(self._h3_text.split())
            if "für " in text and " (" in text:
                self.customer_name = text.split("für ", 1)[1].split(" (")[0].strip()
            return
        if tag == "span" and self._meal_desc_depth > 0:
            self._meal_desc_depth -= 1
            if self._meal_desc_depth == 0 and self._current_calendar and self._current_date:
                text = " ".join("".join(self._meal_text).split())
                if text:
                    cal = self.calendars[self._current_calendar]
                    cal["days"][self._current_date]["meals"].append(text)
            return
        if tag == "div" and self._current_calendar:
            self._calendar_depth -= 1
            if self._calendar_depth == 0:
                self._current_calendar = None
                self._current_date = None

    def finalize(self) -> None:
        """Fold each day's radio state into a plain `attending` bool/None
        (None = no attendance choice exists for that day at all)."""
        for cal in self.calendars.values():
            for date, day in cal["days"].items():
                options = cal["radios"].get(f"meals[{date}]")
                day["attending"] = options.get("angemeldet", False) if options else None


class BlauArtClient:
    """Thin async wrapper around the BlauArt customer portal's HTML forms.

    Relies entirely on the aiohttp session's own cookie jar for the login
    session - no manual cookie handling needed, unlike the exploratory
    curl-based reverse-engineering this was based on.
    """

    def __init__(self, session: ClientSession, customer_number: str, password: str) -> None:
        self._session = session
        self._customer_number = customer_number
        self._password = password

    async def async_login(self) -> str | None:
        """Log in, return the customer's display name if the page shows
        one (not present on every response, callers shouldn't rely on
        this alone to confirm success - see the "Abmelden" check)."""
        async with self._session.post(
            PORTAL_URL,
            data={"login": self._customer_number, "password": self._password},
            headers=HEADERS,
            timeout=TIMEOUT,
        ) as resp:
            if resp.status >= 400:
                raise BlauArtApiError(f"Login request failed: HTTP {resp.status}")
            text = await resp.text()
        if "Abmelden" not in text and "?logout=1" not in text:
            raise BlauArtAuthError("Login failed - check customer number/password")
        parser = _PortalParser()
        parser.feed(text)
        return parser.customer_name

    async def async_fetch_plan(self) -> dict[str, Any]:
        """Fetch the currently visible three months of data. Logs in
        first if the session looks like it isn't (fresh client, or the
        portal's own session/cookie expired since the last poll)."""
        async with self._session.get(PORTAL_URL, headers=HEADERS, timeout=TIMEOUT) as resp:
            if resp.status >= 400:
                raise BlauArtApiError(f"Could not load portal page: HTTP {resp.status}")
            text = await resp.text()
        if "Abmelden" not in text:
            await self.async_login()
            async with self._session.get(PORTAL_URL, headers=HEADERS, timeout=TIMEOUT) as resp:
                text = await resp.text()
        parser = _PortalParser()
        parser.feed(text)
        parser.finalize()
        return {"customer_name": parser.customer_name, "calendars": parser.calendars}

    async def async_set_attendance(self, date: str, attending: bool) -> None:
        """Set whether the child is attending (i.e. gets a meal) on
        `date` ("YYYY-MM-DD"). Re-fetches the plan first so every *other*
        day's current value in that month is preserved - the portal's
        form covers the whole month at once, there's no per-day endpoint.
        """
        plan = await self.async_fetch_plan()
        target_cal = None
        for cal in plan["calendars"].values():
            if date in cal["days"]:
                target_cal = cal
                break
        if target_cal is None:
            raise BlauArtApiError(
                f"{date} isn't in the currently visible date range (portal only shows "
                "the previous/current/next calendar month)"
            )
        if not target_cal["days"][date]["editable"]:
            raise BlauArtApiError(f"{date} is no longer editable in the portal")

        form_data: dict[str, str] = dict(target_cal["hidden"])
        for name, options in target_cal["radios"].items():
            if name == f"meals[{date}]":
                form_data[name] = "angemeldet" if attending else "abgemeldet"
            else:
                checked = next((value for value, is_checked in options.items() if is_checked), None)
                if checked is not None:
                    form_data[name] = checked

        async with self._session.post(
            PORTAL_URL, data=form_data, headers=HEADERS, timeout=TIMEOUT
        ) as resp:
            if resp.status >= 400:
                raise BlauArtApiError(f"Could not submit attendance change: HTTP {resp.status}")
