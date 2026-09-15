# BlauArt Kita-Verpflegung

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

## Installation

Via [HACS](https://hacs.xyz/): add this repository as a custom
repository (category: Integration), then install "BlauArt
Kita-Verpflegung" and restart Home Assistant.

## Configuration

Settings → Devices & Services → Add Integration → "BlauArt
Kita-Verpflegung", then enter your portal customer number and password
(the same ones you use to log into the customer portal directly).
