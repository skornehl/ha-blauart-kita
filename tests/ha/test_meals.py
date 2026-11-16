"""How a meal description is read: label, dish, allergen markers.

Reported on 0.4.2 (2026-11-16): the switch feed's `meals` attribute read
"Essen 1GeflügelfrikadelleGl, W, E, M, N, fl mit ...". The portal puts
the label, the dish and each allergen marker in elements of their own
with no whitespace between them, and the parser joined their text as it
came. The three meals here are the ones from that report. The real page
isn't checked in, so each is built in several plausible markups - and
each markup, joined the old way, gives exactly the reported text.
"""
from __future__ import annotations

import re

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.blauart_kita.api import PORTAL_URL, BlauArtClient, meal_text
from custom_components.blauart_kita.const import DOMAIN
from portal_data import month_tab, render_page

from .conftest import make_entry

# Per meal: the label, then each piece of the dish with its allergens.
MEALS = [
    (
        "Essen 1",
        [
            ("Geflügelfrikadelle", "Gl, W, E, M, N, fl"),
            (" mit Geflügelsoße", "Gl, W, fl"),
            (", Mischgemüse und Bio Reis, Obst, Gemüsestücke", None),
        ],
    ),
    ("Essen 2", [("Rosenkohleintopf, Bauernbrot", "Gl, R, W"), (", Obst, Gemüsestücke", None)]),
    ("Essen 3", [("Salatschale / Salatbuffet", "E, M, fl")]),
]
REPORTED = [
    "Essen 1GeflügelfrikadelleGl, W, E, M, N, fl mit GeflügelsoßeGl, W, fl, Mischgemüse und "
    "Bio Reis, Obst, Gemüsestücke",
    "Essen 2Rosenkohleintopf, BauernbrotGl, R, W, Obst, Gemüsestücke",
    "Essen 3Salatschale / SalatbuffetE, M, fl",
]
EXPECTED = [
    "Essen 1: Geflügelfrikadelle mit Geflügelsoße, Mischgemüse und Bio Reis, Obst, Gemüsestücke",
    "Essen 2: Rosenkohleintopf, Bauernbrot, Obst, Gemüsestücke",
    "Essen 3: Salatschale / Salatbuffet",
]

LABEL_MARKUP = {
    "strong": "<strong>{}</strong>",
    "span": '<span class="gw-meal-name">{}</span>',
    "br": "{}<br>",
}
ALLERGEN_MARKUP = {
    "span": '<span class="gw-allergens">{}</span>',
    "sup": "<sup>{}</sup>",
}


def markup(label_markup: str, allergen_markup: str, label: str, pieces) -> str:
    html = label_markup.format(label)
    for text, allergens in pieces:
        html += text
        if allergens:
            html += allergen_markup.format(allergens)
    return html


def page(meals_html: list[str]) -> str:
    """November with the reported meals on Monday 2026-11-16."""
    tabs = {"calendar-1": month_tab(2026, 11)}
    tabs["calendar-1"]["days"]["2026-11-16"]["meals_html"] = meals_html
    return render_page(tabs)


@pytest.mark.parametrize("allergen", ALLERGEN_MARKUP)
@pytest.mark.parametrize("label", LABEL_MARKUP)
async def test_reported_meals(hass: HomeAssistant, aioclient_mock, label, allergen) -> None:
    meals_html = [markup(LABEL_MARKUP[label], ALLERGEN_MARKUP[allergen], *meal) for meal in MEALS]
    # The markup is what was reported: all its text joined as it comes.
    assert [" ".join(re.sub(r"<[^>]+>", "", html).split()) for html in meals_html] == REPORTED

    aioclient_mock.get(PORTAL_URL, text=page(meals_html))
    client = BlauArtClient(async_get_clientsession(hass), "12345", "secret")
    plan = await client.async_fetch_plan()
    assert plan["calendars"]["calendar-1"]["days"]["2026-11-16"]["meals"] == EXPECTED


async def test_switch_and_sensor_show_readable_meals(hass: HomeAssistant, aioclient_mock) -> None:
    meals_html = [markup(LABEL_MARKUP["strong"], ALLERGEN_MARKUP["span"], *meal) for meal in MEALS]
    aioclient_mock.get(PORTAL_URL, text=page(meals_html))
    entry = make_entry(auto_deregister=False)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    switch = er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_day_0")
    assert hass.states.get(switch).attributes["meals"] == (
        "- Essen 1: Geflügelfrikadelle mit Geflügelsoße, Mischgemüse und Bio Reis, Obst, Gemüsestücke\n"
        "- Essen 2: Rosenkohleintopf, Bauernbrot, Obst, Gemüsestücke\n"
        "- Essen 3: Salatschale / Salatbuffet"
    )
    assert hass.states.get("sensor.max_mustermann_today").state == EXPECTED[0]


@pytest.mark.parametrize(
    ("chunks", "text"),
    [
        ([], ""),
        ([("Tagessuppe", False)], "Tagessuppe"),
        ([("Essen 4", False)], "Essen 4"),
        ([("Essen 12", False), ("  ", False), ("Lasagne", False)], "Essen 12: Lasagne"),
        # A label in a marker element is still the label.
        ([("Essen 2", True), ("Suppe", False), ("Gl", True)], "Essen 2: Suppe"),
        # Only markers: then they are the dish, not allergens.
        ([("Essen 1", False), ("Nudeln", True), ("mit Soße", True)], "Essen 1: Nudeln mit Soße"),
        # "Essen" that isn't a label stays part of the text.
        ([("Essen auf Rädern", False)], "Essen auf Rädern"),
    ],
)
def test_meal_text(chunks, text) -> None:
    assert meal_text(chunks) == text


def test_text_split_across_reads() -> None:
    """A page can arrive in pieces; a word split between them stays one."""
    from custom_components.blauart_kita.api import _PortalParser

    html = page([markup(LABEL_MARKUP["strong"], ALLERGEN_MARKUP["span"], *MEALS[0])])
    cut = html.index("frikadelle")
    parser = _PortalParser()
    parser.feed(html[:cut])
    parser.feed(html[cut:])
    parser.finalize()
    assert parser.calendars["calendar-1"]["days"]["2026-11-16"]["meals"] == EXPECTED[:1]
