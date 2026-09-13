#!/usr/bin/env python3
"""
Plane Spotter — what is flying over the house, on the desk radar.

Data: adsb.lol — free, no key, no account. Community ADS-B receivers.
  https://api.adsb.lol/v2/point/LAT/LON/RADIUS_NM

The board is sent the aircraft's position as km EAST and km NORTH of the house,
plus its track and ground speed. Between updates the board DEAD-RECKONS: it
advances the icon along its own heading at its own speed, ~2.5 times a second,
and snaps to truth on the next poll. That is why the plane crawls across the
screen instead of jumping — the same trick a real radar display uses.

  ./spotter.py             one look
  ./spotter.py --watch     keep watching, updating the radar
  ./spotter.py --clear     clear the radar
"""
import sys, os, json, math, time, subprocess, urllib.request
from pathlib import Path

def _cfg(name, default):
    """Location stays OUT of the source — env var, else ~/.config/rangerpuck/spotter.env,
    else the default. Same file siren.py reads, so one place sets every collector.
    The default is Dublin Airport: the tool does something sensible unconfigured,
    and a clone of this never carries anybody's address."""
    v = os.environ.get(name)
    if v: return v
    f = Path.home() / ".config/rangerpuck/spotter.env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{name}=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default

LAT      = float(_cfg("SPOTTER_LAT", 53.4264))     # Dublin Airport
LON      = float(_cfg("SPOTTER_LON", -6.2499))
RANGE_KM = float(_cfg("SPOTTER_RANGE_KM", 40))
SEND = Path.home() / "esp32-projects/1-ranger-puck/tools/send.sh"
CACHE = Path.home() / ".ranger-memory/config/rangerpuck.ip"

class FeedDown(Exception):
    """adsb.lol is unreachable, rate-limiting, or returning something unusable.
    A named exception so callers can report "the feed is down" rather than either
    crashing or — worse — treating it as an empty sky."""

def fetch():
    url = f"https://api.adsb.lol/v2/point/{LAT}/{LON}/{int(RANGE_KM/1.852)}"
    req = urllib.request.Request(url, headers={"User-Agent": "plane-spotter/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r).get("ac", [])
    except Exception as e:
        # NEVER return [] here. An empty list means "clear sky", which is a
        # statement about the world; this is "we do not know". Conflating them
        # would clear the radar on every network blip.
        raise FeedDown(f"{type(e).__name__}: {e}") from e

def offsets(lat, lon):
    """km east / km north of the house — flat-earth is fine over 40km."""
    return ((lon - LON) * 111.32 * math.cos(math.radians(LAT)),
            (lat - LAT) * 110.57)

# Callsign prefix -> who it actually is. Nicer than a code on the screen.
AIRLINES = {
 "RYR":"Ryanair","EIN":"Aer Lingus","BAW":"British Airways","UAE":"Emirates",
 "AFR":"Air France","DLH":"Lufthansa","KLM":"KLM","EZY":"easyJet","WZZ":"Wizz",
 "AAL":"American","UAL":"United","DAL":"Delta","ACA":"Air Canada","VIR":"Virgin",
 "IBE":"Iberia","VLG":"Vueling","TAP":"TAP","SWR":"Swiss","AUA":"Austrian",
 "ETD":"Etihad","QTR":"Qatar","SIA":"Singapore","THY":"Turkish","FDX":"FedEx",
 "UPS":"UPS","GTI":"Atlas","CGI":"Coast Guard","IRC":"Irish Coast Gd",
 "RRR":"RAF","IAM":"Irish Air Corps","NJE":"NetJets","BCS":"DHL",
}
# ── route lookup ────────────────────────────────────────────────────────────
# ADS-B does NOT broadcast a destination — the transponder has no idea where the
# aircraft is going. adsb.lol publishes a separate route endpoint keyed on
# callsign. A route does not change mid-flight, so it is cached for the session
# and only the PRIMARY aircraft is looked up: one extra request per poll at most,
# against a free community API.
_routes = {}

def route_for(cs):
    """-> {'from','to','from_name','to_name','from_cc','to_cc'} or None.

    The API returns full airport names and cities as well as the codes, so keep
    both: the board wants 'LIS > DUB' because the screen is 1.47 inches, but a
    terminal has room for 'Lisbon, PT -> Dublin, IE' — which is what you actually
    want to read when you hear one overhead.
    Cached; never raises.
    """
    if not cs or not cs[:3].isalpha(): return None
    if cs in _routes: return _routes[cs]
    try:
        req = urllib.request.Request(f"https://api.adsb.lol/api/0/route/{cs}",
                                     headers={"User-Agent": "plane-spotter/0.1"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
    except Exception:
        # DO NOT cache this. "I could not ask" is not "there is no route" — and
        # the first version cached the timeout permanently, so a slow first call
        # meant that flight never showed a destination for the rest of the
        # session. Absence of an answer is not a fact.
        return None
    iata = d.get("_airport_codes_iata") or ""
    if "-" not in iata:
        _routes[cs] = None                   # asked, genuinely no route — cache that
        return None
    a, b = [x.strip()[:4] for x in iata.split("-", 1)]
    ports = {p.get("iata"): p for p in (d.get("_airports") or []) if p.get("iata")}
    def nm(code):
        p = ports.get(code, {})
        return (p.get("location") or p.get("name") or code, p.get("countryiso2") or "")
    fn, fc = nm(a); tn, tc = nm(b)
    _routes[cs] = {"from": a, "to": b, "from_name": fn, "to_name": tn,
                   "from_cc": fc, "to_cc": tc}
    return _routes[cs]

def route_short(cs):
    """'LIS>DUB' for the board, or '' — the screen has no room for names."""
    r = route_for(cs)
    return f"{r['from']}>{r['to']}" if r else ""

def _ascii(s):
    """Fold to plain ASCII for the board.

    The screen's font is a 5x7 CP437 BITMAP, not Unicode. 'Montréal' is
    'Montr\xc3\xa9al' in UTF-8 — two bytes the renderer draws as two unrelated
    glyphs, so the board showed mojibake where a city name should be. The
    TERMINAL keeps the real accents (see route_long); only the board is folded.
    """
    import unicodedata
    out = []
    for ch in s:
        if ch in "\u00d8\u00f8": out.append("O" if ch.isupper() else "o"); continue
        if ch in "\u00c6\u00e6": out.append("AE" if ch.isupper() else "ae"); continue
        if ch == "\u00df":        out.append("ss"); continue
        if ch in "\u0141\u0142": out.append("L" if ch.isupper() else "l"); continue
        if ch in "\u0110\u0111": out.append("D" if ch.isupper() else "d"); continue
        d = unicodedata.normalize("NFKD", ch)
        out.append("".join(c for c in d if not unicodedata.combining(c)))
    return "".join(out).encode("ascii", "replace").decode("ascii")

def route_board(cs, width=52):
    """'YUL Montreal, CA > CDG Paris, FR' — code, city AND country.

    The country is the half that makes a code mean something: 'CFU' is noise,
    'CFU Kerkyra Island, GR' is a place you can picture. But the panel is only so
    wide, so this DEGRADES rather than truncating mid-word — a name cut to
    'Kerkyra Isla' is worse than no name at all.

    width is the character budget at text size 1: about 52 in landscape (320px /
    6px per char) and 28 in portrait (172px).
    """
    r = route_for(cs)
    if not r: return ""
    fn, tn = _ascii(r["from_name"]), _ascii(r["to_name"])
    fc, tc = r["from_cc"], r["to_cc"]
    # most informative first; take the first that fits
    for txt in (
        f"{r['from']} {fn}{', ' + fc if fc else ''} > {r['to']} {tn}{', ' + tc if tc else ''}",
        f"{r['from']} {fn} > {r['to']} {tn}",
        f"{r['from']} > {r['to']} {tn}",
        f"{r['from']} > {r['to']}",
    ):
        if len(txt) <= width: return txt
    return f"{r['from']}>{r['to']}"[:width]

def route_long(cs):
    """'Lisbon, PT -> Dublin, IE' for the terminal, or ''."""
    r = route_for(cs)
    if not r: return ""
    f = r["from_name"] + (f", {r['from_cc']}" if r["from_cc"] else "")
    t = r["to_name"]   + (f", {r['to_cc']}"   if r["to_cc"]   else "")
    return f"{f} → {t}"

def airline(cs):
    return AIRLINES.get(cs[:3].upper(), "")

def geometry(a):
    """Is it coming or going, how close will it get, and when?

    Velocity vector dotted with the vector TO the house: positive means closing.
    Closest point of approach is straightforward vector maths, and it answers the
    question you actually have — 'will this one come over me, and when?'
    """
    e, n = a["_e"], a["_n"]                       # km east / north of the house
    d = math.hypot(e, n)
    kmh = (a.get("gs") or 0) * 1.852
    tr = math.radians(a.get("track") or 0)
    ve, vn = math.sin(tr) * kmh, math.cos(tr) * kmh     # km/h
    # vector from plane to house is (-e, -n)
    closing = (ve * -e + vn * -n)
    approaching = closing > 0
    sp2 = ve*ve + vn*vn
    if sp2 < 1:
        return d, approaching, d, None
    t = max(0.0, closing / sp2)                        # hours to closest approach
    cpa = math.hypot(e + ve*t, n + vn*t)
    return d, approaching, cpa, t * 60.0               # minutes

def compass(d):
    return ["N","NNE","NE","ENE","E","ESE","SE","SSE",
            "S","SSW","SW","WSW","W","WNW","NW","NNW"][int((d + 11.25) % 360 // 22.5)]

AIRBORNE_FT = 300      # below this it is taxiing or on the roll at Dublin, not overhead
GONE_KM     = 12       # once it is past AND this far, it is somebody else's plane now

def interest(a, d):
    """Which aircraft is worth showing?

    Not simply the nearest, and not simply the lowest. A Ryanair on the takeoff
    roll at Dublin is 75ft and 17km away — low and close, and completely
    uninteresting. What you want is the one you can HEAR AND SEE from the garden:
    genuinely airborne, low, and near.
    """
    alt = a.get("alt_baro") or 0
    if alt < AIRBORNE_FT:
        return -1000                         # on the ground, never show it
    score = 100 - d * 3                      # proximity matters most
    if not a.get("_approaching", True): score -= 35   # going away from you
    # Audibility, not just proximity. Above ~15,000ft you will not hear it no
    # matter how close it looks on a map — a cruising jet directly overhead is
    # silent. The question is "what am I hearing", so weight for that.
    if alt < 4000:    score += 45            # low enough to hear clearly
    elif alt < 10000: score += 15
    elif alt > 20000: score -= 25            # cruising, inaudible
    if a.get("t") in ("A189","S92","H145","EC45","A139"): score += 30   # rotary, usually SAR
    if d > 35: score -= 30                   # edge of range, barely yours
    return score

def pick():
    ac = [a for a in fetch() if isinstance(a.get("alt_baro"), (int, float))
          and a.get("lat") and a.get("lon") and a["alt_baro"] >= AIRBORNE_FT]
    if not ac: return None
    for a in ac:
        e, n = offsets(a["lat"], a["lon"])
        a["_e"], a["_n"] = e, n
        a["_d"] = math.hypot(e, n)
    # Prefer something coming TOWARDS you. But an empty radar is a worse answer
    # than a dimmed one, so if nothing is inbound, show the nearest airborne
    # aircraft anyway and let the screen label it "outbound".
    for a in ac:
        _, approaching, _, _ = geometry(a)
        a["_approaching"] = approaching
    # Score EVERYTHING and sort by score. An earlier version split the list into
    # an "inbound pool" first, which let a high-altitude overflight 8km away win
    # simply by being in the pool — its low score never got a say. Inbound vs
    # outbound is already worth -35 in the score; it does not need a second,
    # stronger vote that overrides everything else.
    ac.sort(key=lambda a: -interest(a, a["_d"]))
    any_inbound = any(a["_approaching"] for a in ac)

    # keep the aircraft we are already following, unless it is gone or clearly beaten
    now = time.time()
    held = next((a for a in ac if (a.get("flight") or "").strip() == _sticky["cs"]), None)
    if held is not None and now - _sticky["since"] < STICKY_SECONDS:
        best = interest(ac[0], ac[0]["_d"])
        if best - interest(held, held["_d"]) < STICKY_MARGIN:
            ac.remove(held); ac.insert(0, held)          # hold focus
    chosen = (ac[0].get("flight") or "").strip()
    if chosen != _sticky["cs"]:
        _sticky["cs"], _sticky["since"] = chosen, now
    return ac, len(ac), any_inbound

def as_contact(a):
    d, approaching, cpa, tmin = geometry(a)
    cs = (a.get("flight") or "?").strip()
    prim = a.get("_primary")
    return {"route": route_short(cs) if prim else "",
            # two budgets, because the board cannot tell Python its rotation:
            # 52 chars for landscape, 28 for portrait. Firmware picks one.
            "routefull": route_board(cs, 52) if prim else "",
            "routemid":  route_board(cs, 28) if prim else "",
            "east": round(a["_e"], 2), "north": round(a["_n"], 2),
            "track": a.get("track", 0), "gs": a.get("gs", 0),
            "alt": int(a["alt_baro"]), "cs": cs, "type": a.get("t", ""),
            "dist": round(d, 1), "approaching": bool(approaching),
            "cpa": round(cpa, 1), "eta": round(tmin) if tmin is not None else -1,
            "vs": int(a.get("baro_rate") or 0), "airline": airline(cs)}

def push(planes):
    """planes[0] is the primary — labelled, trailed, and on the gauge."""
    for i, a in enumerate(planes): a["_primary"] = (i == 0)
    payload = json.dumps({"planes": [as_contact(a) for a in planes[:10]],
                          "range": RANGE_KM})
    host = CACHE.read_text().strip() if CACHE.exists() else "rangerpuck.local"
    for h in (host, "rangerpuck.local"):
        try:
            req = urllib.request.Request(f"http://{h}/plane", data=payload.encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=4).read()
            return True
        except Exception:
            continue
    return False

def clear():
    host = CACHE.read_text().strip() if CACHE.exists() else "rangerpuck.local"
    try:
        req = urllib.request.Request(f"http://{host}/plane",
                                     data=b'{"clear":true}',
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=4).read(); print("  radar cleared")
    except Exception as e:
        print(f"  could not clear: {e}")

def once(quiet=False):
    r = pick()
    if not r:
        if not quiet: print("  sky is empty — nothing airborne within range")
        return None
    allac, total, any_inbound = r
    a = allac[0]
    cs = (a.get("flight") or "?").strip()
    ok = push(allac)
    d, approaching, cpa, tmin = geometry(a)
    vs = a.get("baro_rate") or 0
    trend = "climbing" if vs > 200 else "descending" if vs < -200 else "level"
    who = airline(cs)
    if not quiet:
        brg = compass(math.degrees(math.atan2(a["_e"], a["_n"])) % 360)
        tcol = C['y'] if trend == "descending" else C['c'] if trend == "climbing" else C['d']
        dcol = C['r'] if d < 5 else C['y'] if d < 15 else C['c']

        print()
        name = f"{C['B']}{C['w']}{cs}{C['N']}" + (f"{C['d']} · {C['N']}{who}" if who else "")
        rt = route_for(cs)
        routetxt = (f"   {C['g']}{rt['from']} → {rt['to']}{C['N']}") if rt else ""
        print(f"  ✈️  {name}  {C['d']}{a.get('t','')}{C['N']}{routetxt}"
              f"   {C['w']}{heading_arrow(a.get('track',0))}{C['N']}"
              f"{C['d']} {compass(a.get('track',0))}{C['N']}")
        long = route_long(cs)
        if long:
            arriving = "arriving from" if (a.get("baro_rate") or 0) < -200 else "flying"
            print(f"      {C['g']}{arriving}  {long}{C['N']}")
        print(f"      {tcol}{ARROW[trend]} {int(a['alt_baro']):,} ft {trend}{C['N']}"
              f"{C['d']} · {C['N']}{a.get('gs',0):.0f} kt"
              f"{C['d']} · {C['N']}{dcol}{d:.1f} km {brg}{C['N']}"
              f"{C['d']} · tracking {a.get('track',0):.0f}° {compass(a.get('track',0))}{C['N']}")
        if approaching and tmin is not None:
            near = C['g'] if cpa < 5 else C['y'] if cpa < 15 else C['d']
            look = "  ← look up" if cpa < 5 and tmin < 10 else ""
            print(f"      {near}▸ closest {cpa:.1f} km in {tmin:.0f} min{look}{C['N']}")
        else:
            print(f"      {C['d']}▸ outbound, heading away{C['N']}")

        others = allac[1:9]
        if others:
            print(f"\n    {C['d']}also in range{C['N']}")
            print(f"    {C['d']}{'':<3}{'callsign':<10}{'route':<9}{'airline':<13}{'type':<5}"
                  f"{'altitude':>9}{'dist':>8} {'brg':<5}{C['N']}")
            for o in others:
                od, oapp, _, _ = geometry(o)
                ocs = (o.get("flight") or "?").strip()
                ovs = o.get("baro_rate") or 0
                ot = "climbing" if ovs > 200 else "descending" if ovs < -200 else "level"
                obrg = compass(math.degrees(math.atan2(o["_e"], o["_n"])) % 360)
                # one arrow for climb/descent (next to the altitude, where it means
                # something) and a word for inbound/outbound. Two arrows side by
                # side just read as noise.
                acol = C['y'] if ot == "descending" else C['c'] if ot == "climbing" else C['d']
                body = C['N'] if oapp else C['d']
                ohdg = heading_arrow(o.get("track", 0))
                # cache-only: a route already fetched costs nothing to show, and we
                # do not fire extra requests at a free community API for the list
                orte = _routes.get(ocs)
                otxt = f"{orte['from']}>{orte['to']}" if orte else ""
                print(f"    {body}✈{C['N']}{C['w'] if oapp else C['d']}{ohdg}{C['N']} "
                      f"{body}{ocs:<10}{C['N']}{C['g'] if orte else ''}{otxt:<9}{C['N']}"
                      f"{body}{(airline(ocs) or '·'):<13}{C['N']}"
                      f"{body}{o.get('t',''):<5}{C['N']}"
                      f"{acol}{ARROW[ot]}{C['N']}{body}{int(o['alt_baro']):>7,}ft{C['N']}"
                      f"{body}{od:>7.1f}km {obrg:<4}{C['N']}"
                      + (f"{C['g']}inbound{C['N']}" if oapp else f"{C['d']}outbound{C['N']}"))

        extra = "" if any_inbound else f"  {C['d']}(nothing inbound){C['N']}"
        print(f"\n  {C['d']}{total} contacts{C['N']}{extra}"
              f"   {C['g'] + '→ radar' + C['N'] if ok else C['r'] + '(puck unreachable)' + C['N']}")
    return a

C = dict(r='\033[31m', g='\033[32m', y='\033[33m', c='\033[36m',
         w='\033[97m', d='\033[90m', B='\033[1m', N='\033[0m')
ARROW = {"climbing": "▲", "descending": "▼", "level": "—"}

def heading_arrow(deg):
    """An arrow pointing the way the aircraft is actually going.

    Same idea as the rotated icon on the puck, in eight directions — enough to
    read the shape of the traffic at a glance. Dublin's arrivals all point the
    same way, so a column of matching arrows IS the approach line.
    """
    return "↑↗→↘↓↙←↖"[int(((deg % 360) + 22.5) % 360 // 45)]

# ── stickiness ───────────────────────────────────────────────────────────────
# Without this the primary swaps every poll as aircraft trade places in the
# scoring, and the label jumps around while you are trying to read it. Once a
# plane is chosen, keep it until it leaves range, lands, or something clearly
# more interesting turns up. Aircraft take minutes to cross; the display should
# not change its mind every twenty seconds.
STICKY_SECONDS   = 90      # how long to hold a chosen aircraft
STICKY_MARGIN    = 25      # how much better a rival must score to steal focus
_sticky = {"cs": None, "since": 0.0}

OFF_FLAG = Path.home() / ".ranger-memory/config/.radar-off"

if __name__ == "__main__":
    try:
        if "--off" in sys.argv:
            OFF_FLAG.parent.mkdir(parents=True, exist_ok=True); OFF_FLAG.touch()
            clear(); print("  radar OFF — ./spotter.py --on to bring it back"); sys.exit()
        if "--on" in sys.argv:
            OFF_FLAG.unlink(missing_ok=True); print("  radar ON"); sys.exit()
        if OFF_FLAG.exists() and "--clear" not in sys.argv:
            print("  radar is OFF  (./spotter.py --on)"); sys.exit()
        if "--clear" in sys.argv: clear(); sys.exit()
        if "--watch" in sys.argv:
            print("  watching — ctrl-c to stop\n")
            last = None
            while True:
                if OFF_FLAG.exists():
                    print("  radar switched off — stopping"); clear(); break
                try:
                    a = once()
                    if a: last = (a.get("flight") or "").strip()
                except FeedDown as e: print(f"  feed down — {e}")
                except Exception as e: print(f"  {type(e).__name__}: {e}")
                time.sleep(20)
        else:
            # launchd runs THIS path. It had no handler, so a feed outage was an
            # uncaught traceback straight into the log (and the log used to be
            # /dev/null). Report it and exit non-zero so the caller can tell.
            try:
                once()
            except FeedDown as e:
                print(f"  adsb.lol unavailable — {e}", file=sys.stderr)
                sys.exit(1)
    except KeyboardInterrupt:
        print("\n  Radar watch stopped" if "--watch" in sys.argv else "\n  stopped")
        sys.exit(0)
