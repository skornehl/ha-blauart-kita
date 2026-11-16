# BlauArt Kita-Verpflegung

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5?style=for-the-badge&logo=homeassistantcommunitystore&logoColor=white)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Release](https://img.shields.io/github/v/release/skornehl/ha-blauart-kita?style=for-the-badge&color=6366F1)](https://github.com/skornehl/ha-blauart-kita/releases)
[![Home Assistant 2026.1+](https://img.shields.io/badge/Home%20Assistant-2026.1%2B-18BCF2?style=for-the-badge&logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)
[![Checks](https://img.shields.io/github/actions/workflow/status/skornehl/ha-blauart-kita/validate.yml?branch=main&style=for-the-badge&label=checks)](https://github.com/skornehl/ha-blauart-kita/actions/workflows/validate.yml)
[![License](https://img.shields.io/github/license/skornehl/ha-blauart-kita?style=for-the-badge&color=9333EA)](LICENSE)

Home Assistant custom integration for the [BlauArt Kita-/Schulverpflegung
customer portal](https://www.blauart.de/kita-schulverpflegung/kundenportal/) -
shows the daily meal plan and lets you register/deregister attendance
straight from Home Assistant, instead of logging into the portal by hand.

There's no public API for this portal; the integration logs in with your
customer number and password (an HTML session, exactly like using the
portal in a browser) and parses the same page you'd see there.

## Features

- **Calendar entity** listing every visible day (previous/current/next
  calendar month, same range the portal itself shows) with that day's
  meal option(s) as an all-day event - use the built-in Lovelace calendar
  card for a "what's for lunch" view.
- **Today / Tomorrow sensors** with the meal name as state and the full
  meal list, attendance, and editability as attributes.
- **A 14-weekday switch feed** (today + the next 13 school days -
  weekends are skipped entirely), one switch per day, to register (on)
  or deregister (off) attendance directly. Unavailable once the portal
  itself no longer allows changing that day, or on a day with no meal at
  all (a holiday). Toggling is optimistic: the switch flips instantly and
  the actual portal sync (a slow two-step fetch-then-resubmit, several
  seconds) happens in the background - a tap should feel instant, not
  make anyone wait out a round trip. On sync failure, a persistent
  notification is raised and the switch reverts to its real state.
  Meant to be used as a plain scrollable stack (markdown text + a `tile`
  toggle per day) - simple enough for a child to use on their own
  account.
- **`blauart_kita.set_attendance` service** to change attendance for any
  other currently-editable date.
- **Refresh button** to force an immediate portal fetch instead of
  waiting out the 6h poll interval - `homeassistant.update_entity` is a
  no-op for coordinator-backed entities on recent Home Assistant, so
  this is a dedicated `button` entity instead.
- **"Automatisch abmelden" option** (off by default): deregisters every
  day that shows up in the portal for the first time, so a newly
  unlocked month starts with no meals booked and you register only the
  ones you want. See [Options](#options).

## Installation

Via [HACS](https://hacs.xyz/): add this repository as a custom
repository (category: Integration), then install "BlauArt
Kita-Verpflegung" and restart Home Assistant.

## Configuration

Settings → Devices & Services → Add Integration → "BlauArt
Kita-Verpflegung", then enter your portal customer number and password
(the same ones you use to log into the customer portal directly).

## Options

Settings → Devices & Services → BlauArt Kita-Verpflegung → Configure.

**Automatisch abmelden** (deregister automatically, off by default):
days that appear in the portal for the first time - usually the month
the portal unlocks at the start of each month - are deregistered right
away, all of them in one submit per month. A persistent notification
says which days were deregistered, so you know it's time to register
the meals you want.

- Only *new* days are touched. The integration remembers every day it
  has seen (in `.storage/blauart_kita.<entry id>`, so this survives
  restarts); a day it already knows is never changed again, whatever
  its state - your own changes, in the portal or from Home Assistant,
  stay as they are.
- Nothing is deregistered retroactively. Days are remembered while the
  option is off too, so switching it on only affects days that show up
  afterwards. The very first poll after installing or updating just
  records what is visible.
- Days without an attendance choice (weekends, holidays) are skipped,
  and so are days the portal no longer lets you change.
- New days are found on the regular poll (every 6 hours) or when you
  press the refresh button. A day you register in the portal *before*
  the integration has seen it still counts as new.
- If the portal can't be reached, a notification says so and the next
  poll tries again. If the portal accepts the submit but doesn't apply
  it, you get a notification and the days are not resent.
- Removing the integration forgets the remembered days.

### Trying it out

1. Update to 0.5.0 and restart Home Assistant. The first poll records
   the current months; nothing is changed.
2. Switch on "Automatisch abmelden" under Configure.
3. When the portal shows a new month (at the start of a month), the
   next poll or a press of the refresh button deregisters its days and
   raises the notification. Check the result in the portal.
