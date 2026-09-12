# ✈️ plane-spotter

**You hear a plane. What is it, and where's it going?**

A terminal ADS-B readout for whatever is flying over your house — callsign, airline,
type, altitude, heading, distance, and when it'll be closest. Optionally draws it on a
small ESP32 radar screen.

No API key. No account. Standard library only.

```
  ✈️  EIN179 · Aer Lingus  A21N   ← W
      ▼ 625 ft descending · 130 kt · 15.9 km NNE · tracking 275° W
      ▸ closest 2.1 km in 3 min  ← look up

    also in range
       callsign  airline      type  altitude    dist brg
    ✈↘ RYR7UX    Ryanair      B38M  ▲  5,275ft  27.9km NNW  outbound
    ✈↑ BCS360    DHL          A333  — 34,000ft  16.7km NW   outbound

  3 contacts   → radar
```

## Install

```bash
git clone https://github.com/davidtkeane/plane-spotter
cd plane-spotter
mkdir -p ~/.config/rangerpuck
cat > ~/.config/rangerpuck/spotter.env <<EOF
SPOTTER_LAT=53.4264
SPOTTER_LON=-6.2499
SPOTTER_RANGE_KM=40
EOF
./spotter.py
```

Python 3.9+, nothing to install. Your location lives in the config file, not the source.

```
./spotter.py            one look
./spotter.py --watch    keep watching, updating every 20s
./spotter.py --off      stop (and clear the radar)
./spotter.py --on       start again
```

## It does not simply show the nearest

The nearest aircraft is usually the wrong answer. A 787 at 39,000 ft directly overhead is
a silent dot; an A321 at 1,800 ft twenty kilometres out is about to cross your roof.

So it scores for **what you'll actually see and hear**:

| | |
|---|---|
| proximity | `100 − distance×3` |
| under 4,000 ft | **+45** — low enough to hear |
| 4,000–10,000 ft | +15 |
| above 20,000 ft | **−25** — cruising, inaudible |
| helicopter / SAR | +30 |
| heading away | −35 |
| beyond 35 km | −30 |
| below 300 ft | excluded — that's taxiing |

Real example: a Ryanair at 18.6 km lost to an Aer Lingus at 19.1 km, because the Ryanair
was leaving and the Aer Lingus was descending towards the house. Half a kilometre further
away, and the right answer.

## Closest approach

For anything inbound it gives the **closest point of approach and when** — plain vector
maths on the velocity. `closest 2.1 km in 3 min` means go outside and look up.

## The optional radar

With a [RangerPuck](https://github.com/davidtkeane/rangerpuck) on your network, every
contact is drawn at its real bearing and distance, **rotated to its actual heading**, with
range rings and a distance gauge.

The board **dead-reckons between updates** — it advances each icon along its own track at
its own ground speed a few times a second, then snaps to truth on the next poll. That's
why aircraft crawl across the screen instead of jumping every 20 seconds. It stops
extrapolating after 45 seconds and marks itself `stale`, because a jet at 400 kt covers
7 km a minute and a guess that old is fiction.

Without a board, everything still works in the terminal.

## Data

Aircraft data © [adsb.lol](https://adsb.lol) and its feeder community, made available
under the [Open Database License (ODbL) v1.0](https://opendatacommons.org/licenses/odbl/1-0/)
— the same licence OpenStreetMap uses. This tool queries the live API and does not
redistribute the database.

adsb.lol's own terms: *"You can use the API for free. In the future, you will require an
API key which you can get by feeding to adsb.lol. If you want to use the API for production
purposes, please contact me so I do not break your application by accident."* Rate limits
are dynamic, not published.

**Be decent about it.** This is volunteers' own hardware, bandwidth and electricity.
`--watch` polls every 20 seconds, and a failed request still waits the full 20 before
retrying. Don't make it faster, and don't point a fleet of these at them.

Coverage is whatever volunteers near you provide. Aircraft without ADS-B, or below
receiver coverage, simply won't appear.

MIT. Use it for anything.
