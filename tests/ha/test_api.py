"""BlauArtClient against portal HTML: what a change actually submits."""
from __future__ import annotations

import asyncio

from aiohttp import ClientConnectionError
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.blauart_kita.api import (
    PORTAL_URL,
    BlauArtApiError,
    BlauArtAuthError,
    BlauArtClient,
)
from portal_data import month_tab, render_page, set_day, weekdays


def calendars() -> dict:
    tabs = {f"calendar-{i}": month_tab(2026, m) for i, m in enumerate((10, 11, 12), 1)}
    set_day(tabs["calendar-2"], "2026-11-17", False)
    return tabs


def submitted(aioclient_mock) -> list[dict[str, str]]:
    """The form data of every POST the client sent."""
    return [dict(data) for method, _url, data, _headers in aioclient_mock.mock_calls if method == "POST"]


async def test_fetch_plan_parses_the_page(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(PORTAL_URL, text=render_page(calendars()))
    client = BlauArtClient(async_get_clientsession(hass), "12345", "secret")

    plan = await client.async_fetch_plan()
    assert plan["customer_name"] == "Max Mustermann"
    nov = plan["calendars"]["calendar-2"]
    assert nov["days"]["2026-11-17"] == {
        "editable": True,
        "meals": ["Essen am 2026-11-17"],  # allergen marker left out
        "attending": False,
    }
    assert nov["days"]["2026-11-21"]["attending"] is None
    assert nov["hidden"]["month"] == "2026-11"


async def test_set_attendances_one_submit_per_month(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(PORTAL_URL, text=render_page(calendars()))
    aioclient_mock.post(PORTAL_URL, text="ok")
    client = BlauArtClient(async_get_clientsession(hass), "12345", "secret")

    await client.async_set_attendances({day: False for day in weekdays(2026, 12)} | {"2026-11-30": False})

    forms = submitted(aioclient_mock)
    assert sorted(form["month"] for form in forms) == ["2026-11", "2026-12"]
    nov = next(form for form in forms if form["month"] == "2026-11")
    dec = next(form for form in forms if form["month"] == "2026-12")
    assert nov["meals[2026-11-30]"] == "abgemeldet"
    assert nov["meals[2026-11-17]"] == "abgemeldet"  # kept as it was
    assert nov["meals[2026-11-18]"] == "angemeldet"
    assert all(dec[f"meals[{day}]"] == "abgemeldet" for day in weekdays(2026, 12))
    assert nov["meals[2026-12-01]"] == "angemeldet"  # preview cell, not the owning form
    assert nov["noteditables[2026-11-18]"] == "0" and dec["action"] == "change-meal-plan"


async def test_set_attendance_single_day(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(PORTAL_URL, text=render_page(calendars()))
    aioclient_mock.post(PORTAL_URL, text="ok")
    client = BlauArtClient(async_get_clientsession(hass), "12345", "secret")

    # Dec 1 is also a trailing cell of November's grid - December's form owns it.
    await client.async_set_attendance("2026-12-01", False)
    [form] = submitted(aioclient_mock)
    assert form["month"] == "2026-12" and form["meals[2026-12-01]"] == "abgemeldet"


async def test_set_attendances_refuses_without_submitting(
    hass: HomeAssistant, aioclient_mock
) -> None:
    tabs = calendars()
    tabs["calendar-3"]["hidden"]["noteditables[2026-12-02]"] = "1"
    aioclient_mock.get(PORTAL_URL, text=render_page(tabs))
    aioclient_mock.post(PORTAL_URL, text="ok")
    client = BlauArtClient(async_get_clientsession(hass), "12345", "secret")

    with pytest.raises(BlauArtApiError, match="no longer editable"):
        await client.async_set_attendances({"2026-12-01": False, "2026-12-02": False})
    assert submitted(aioclient_mock) == []


async def test_set_attendances_http_error(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(PORTAL_URL, text=render_page(calendars()))
    aioclient_mock.post(PORTAL_URL, status=502)
    client = BlauArtClient(async_get_clientsession(hass), "12345", "secret")

    with pytest.raises(BlauArtApiError, match="HTTP 502"):
        await client.async_set_attendance("2026-12-01", False)


async def test_set_attendances_network_error(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(PORTAL_URL, text=render_page(calendars()))
    aioclient_mock.post(PORTAL_URL, exc=ClientConnectionError("connection reset"))
    client = BlauArtClient(async_get_clientsession(hass), "12345", "secret")

    with pytest.raises(BlauArtApiError, match="Portal not reachable: connection reset"):
        await client.async_set_attendances({"2026-12-01": False})


async def test_set_attendances_without_changes_sends_nothing(
    hass: HomeAssistant, aioclient_mock
) -> None:
    client = BlauArtClient(async_get_clientsession(hass), "12345", "secret")
    await client.async_set_attendances({})
    assert aioclient_mock.call_count == 0


async def test_changes_take_turns(hass: HomeAssistant) -> None:
    """Two changes at once must not both fetch before either submits,
    and a fetch in between waits for the change before it."""
    events: list[str] = []

    class Response:
        status = 200

        async def __aenter__(self):
            events.append("post")
            await asyncio.sleep(0)
            return self

        async def __aexit__(self, *exc):
            return False

    class Session:
        def post(self, *args, **kwargs):
            return Response()

    client = BlauArtClient(Session(), "12345", "secret")

    async def fetch():
        events.append("fetch")
        await asyncio.sleep(0)
        return {"customer_name": None, "calendars": calendars()}

    client._async_fetch_plan = fetch
    await asyncio.gather(
        client.async_set_attendance("2026-11-18", False),
        client.async_fetch_plan(),
        client.async_set_attendance("2026-11-19", False),
    )
    # A plain fetch waits too, so a refresh never reads half a change.
    assert events == ["fetch", "post", "fetch", "fetch", "post"]


async def test_login(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(PORTAL_URL, text=render_page(calendars()))
    client = BlauArtClient(async_get_clientsession(hass), "12345", "secret")
    assert await client.async_login() == "Max Mustermann"
    [(_method, _url, data, _headers)] = aioclient_mock.mock_calls
    assert data == {"login": "12345", "password": "secret"}


@pytest.mark.parametrize(
    ("response", "error", "message"),
    [
        ({"text": "<html><form>Kundennummer</form></html>"}, BlauArtAuthError, "Login failed"),
        ({"status": 500}, BlauArtApiError, "HTTP 500"),
    ],
)
async def test_login_fails(hass: HomeAssistant, aioclient_mock, response, error, message) -> None:
    aioclient_mock.post(PORTAL_URL, **response)
    client = BlauArtClient(async_get_clientsession(hass), "12345", "secret")
    with pytest.raises(error, match=message):
        await client.async_login()


async def test_fetch_logs_in_when_the_session_expired(hass: HomeAssistant, aioclient_mock) -> None:
    pages = iter(["<html><form>Kundennummer</form></html>", render_page(calendars())])

    async def get(method, url, data):
        return AiohttpClientMockResponse(method, url, text=next(pages))

    aioclient_mock.get(PORTAL_URL, side_effect=get)
    aioclient_mock.post(PORTAL_URL, text=render_page(calendars()))
    client = BlauArtClient(async_get_clientsession(hass), "12345", "secret")

    plan = await client.async_fetch_plan()
    assert [method for method, *_ in aioclient_mock.mock_calls] == ["GET", "POST", "GET"]
    assert plan["calendars"]["calendar-2"]["days"]["2026-11-17"]["attending"] is False


async def test_fetch_http_error(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(PORTAL_URL, status=503)
    client = BlauArtClient(async_get_clientsession(hass), "12345", "secret")
    with pytest.raises(BlauArtApiError, match="HTTP 503"):
        await client.async_fetch_plan()
