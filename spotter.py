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
import sys, os, json, math, time, urllib.request
from pathlib import Path

def _cfg(name, default):
    """Your location stays OUT of the source. Set it in ~/.config/rangerpuck/spotter.env
    or as an environment variable. Default below is Dublin Airport, so the thing
    does something sensible before you configure it."""
    v = os.environ.get(name)
    if v: return v
    f = Path.home() / ".config/rangerpuck/spotter.env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{name}=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default

LAT = float(_cfg("SPOTTER_LAT", 53.4264))      # Dublin Airport — change this
LON = float(_cfg("SPOTTER_LON", -6.2499))
RANGE_KM = float(_cfg("SPOTTER_RANGE_KM", 40))
CACHE = Path.home() / ".config/rangerpuck/ip"

def fetch():
    url = f"https://api.adsb.lol/v2/point/{LAT}/{LON}/{int(RANGE_KM/1.852)}"
    req = urllib.request.Request(url, headers={"User-Agent": "plane-spotter/0.1"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r).get("ac", [])

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
    return ac, len(ac), any_inbound

def as_contact(a):
    d, approaching, cpa, tmin = geometry(a)
    cs = (a.get("flight") or "?").strip()
    return {"east": round(a["_e"], 2), "north": round(a["_n"], 2),
            "track": a.get("track", 0), "gs": a.get("gs", 0),
            "alt": int(a["alt_baro"]), "cs": cs, "type": a.get("t", ""),
            "dist": round(d, 1), "approaching": bool(approaching),
            "cpa": round(cpa, 1), "eta": round(tmin) if tmin is not None else -1,
            "vs": int(a.get("baro_rate") or 0), "airline": airline(cs)}

def push(planes):
    """planes[0] is the primary — labelled, trailed, and on the gauge."""
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
        print(f"  ✈️  {name}  {C['d']}{a.get('t','')}{C['N']}"
              f"   {C['w']}{heading_arrow(a.get('track',0))}{C['N']}"
              f"{C['d']} {compass(a.get('track',0))}{C['N']}")
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
            print(f"    {C['d']}{'':<3}{'callsign':<10}{'airline':<13}{'type':<5}"
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
                print(f"    {body}✈{C['N']}{C['w'] if oapp else C['d']}{ohdg}{C['N']} "
                      f"{body}{ocs:<10}{C['N']}{body}{(airline(ocs) or '·'):<13}{C['N']}"
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

OFF_FLAG = Path.home() / ".config/rangerpuck/radar-off"

if __name__ == "__main__":
    try:
        if "--off" in sys.argv:
            OFF_FLAG.parent.mkdir(parents=True, exist_ok=True)
            OFF_FLAG.touch()
            clear()
            print("  radar OFF — ./spotter.py --on to bring it back")
            sys.exit()

        if "--on" in sys.argv:
            OFF_FLAG.unlink(missing_ok=True)
            print("  radar ON")
            sys.exit()

        if "--clear" in sys.argv:
            clear()
            sys.exit()

        if OFF_FLAG.exists():
            print("  radar is OFF  (./spotter.py --on)")
            sys.exit()

        if "--watch" in sys.argv:
            print("  watching — ctrl-c to stop\n")
            while True:
                if OFF_FLAG.exists():
                    print("  radar switched off — stopping")
                    clear()
                    break
                try:
                    once()
                except Exception as e:
                    # keep the cadence on failure: never hammer a community API
                    print(f"  {type(e).__name__}: {e}")
                time.sleep(20)
        else:
            try:
                once()
            except Exception as e:
                print(f"  could not reach adsb.lol: {type(e).__name__}: {e}")
                sys.exit(1)

    except KeyboardInterrupt:
        print("\n  stopped")
        sys.exit(0)