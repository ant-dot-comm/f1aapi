#!/usr/bin/env python3
"""Fetch layer for motorsportmagazine.com's database (its WordPress JSON API).

motorsportmagazine.com is the source of truth for every season in this API:
per-race results with AUTHORITATIVE points (pole / fastest-lap bonuses included),
full driver names, team and driver URLs (stable identities), qualifying grids and
the final championship table. Every HTTP response is cached under $MM_CACHE so
re-runs are instant and polite; the in-progress season is always refetched.

Identical in f2api and f1a-api (keep in sync). Series rules live in
series_config.py; build_mm.py turns these raw races into the API files.
"""
import json
import os
import re
import tempfile
import time
import unicodedata
import urllib.request
from datetime import datetime

CACHE = os.environ.get("MM_CACHE") or os.path.join(tempfile.gettempdir(), "mm_cache")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
BASE = "https://www.motorsportmagazine.com/database"
AJAX = f"{BASE}/wp-admin/admin-ajax.php"

# Responses for these URL substrings bypass the cache (set by build_mm for live seasons).
REFRESH = set()


def _get(url):
    os.makedirs(CACHE, exist_ok=True)
    key = re.sub(r'[^A-Za-z0-9]+', '_', url)[-190:]
    p = os.path.join(CACHE, key)
    fresh = any(s in url for s in REFRESH)
    if os.path.exists(p) and not fresh:
        return open(p, 'rb').read()
    for att in range(4):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60).read()
            break
        except Exception:
            if att == 3:
                raise
            time.sleep(2 + att)
    open(p, 'wb').write(r)
    time.sleep(0.25)
    return r


def page(url):
    return _get(url).decode('utf-8', 'replace')


def post_id(url):
    m = re.search(r'"post_id":(\d+)', page(url))
    return m.group(1) if m else None


def ajax(action, **params):
    q = "".join(f"&{k}={v}" for k, v in params.items())
    return json.loads(_get(f"{AJAX}?action={action}{q}"))["data"]


def champ_id(slug_name):
    return post_id(f"{BASE}/championships/{slug_name}/")


def season_races(cid):
    return ajax("fetch_database_championship_races", championship_id=cid)


def season_standings(cid):
    return ajax("fetch_database_championship_standings", championship_id=cid)


def race_results(rid):
    return ajax("fetch_database_race_results", race_id=rid, sprint=0, page=1, perPage=100)


def race_quali(rid):
    return ajax("fetch_database_race_qualifying", race_id=rid, sprint=0, page=1, perPage=100)


def race_box(rid):
    return ajax("fetch_database_race_data_box", race_id=rid)


def driver_box(driver_slug):
    """Biography box of a driver page: nationality, date of birth, birthplace."""
    pid = post_id(f"{BASE}/drivers/{driver_slug}/")
    box = ajax("fetch_database_driver_data_box", driver_id=pid) if pid else {}
    country = (box or {}).get("country") or {}
    flag = re.search(r'svgs/([a-z]{2})\.svg', country.get("flag") or "")
    dob = (((box or {}).get("age") or {}).get("dob") or {}).get("date", "")
    return {"nationality": country.get("name") or "", "countryCode": flag.group(1).upper() if flag else "",
            "dateOfBirth": dob[:10] if (box or {}).get("date_of_birth") else "",
            "birthplace": (box or {}).get("birthplace") or ""}


# ---- small helpers -------------------------------------------------------
def slug(s):
    s = ''.join(c for c in unicodedata.normalize('NFD', s or '') if unicodedata.category(c) != 'Mn').lower()
    return re.sub(r'[^a-z0-9]+', '_', s).strip('_')


def url_slug(url, kind):
    """'.../database/drivers/rafael-camara/' -> 'rafael-camara' (kind = drivers/teams/circuits)."""
    m = re.search(rf'/{kind}/([^/]+)/', url or '')
    return m.group(1) if m else ''


def pdate(s):
    try:
        return datetime.strptime((s or '').strip(), "%d/%m/%Y")
    except Exception:
        return None


def t2s(x):
    """'58m 51.044s' / '1h 02m 03.4s' / '1m 27.807s' -> seconds, else None."""
    if not x:
        return None
    x = x.strip()
    h = re.search(r'(\d+)\s*h', x)
    m = re.search(r'(\d+)\s*m(?!s)', x)
    s = re.search(r'([\d.]+)\s*s', x)
    if not s and not m:
        return None
    return (int(h.group(1)) * 3600 if h else 0) + (int(m.group(1)) * 60 if m else 0) + (float(s.group(1)) if s else 0)


def fmt_gap(sec):
    return "" if sec is None else f"+{sec:.3f}"


def fmt_lap(raw):
    """'1m 34.050s' / '1h 02m 03.4s' -> 'M:SS.mmm' / 'H:MM:SS.mmm'."""
    sec = t2s(raw)
    if sec is None:
        return ""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:06.3f}" if h else f"{m}:{s:06.3f}"


# motorsportmag circuit slug -> (circuitId, raceName, locality, country), aligned with the
# ids the API has always used. Unknown slugs fail the build so a new venue gets a row here.
CIRC = {
    "albert-park": ("albert_park", "Australian Grand Prix", "Melbourne", "Australia"),
    "austin-cota": ("americas", "United States Grand Prix", "Austin", "USA"),
    "las-vegas-boulevard": ("vegas", "Las Vegas Grand Prix", "Las Vegas", "USA"),
    "madrid": ("madring", "Spanish Grand Prix", "Madrid", "Spain"),
    "miami-international-autodrome": ("miami", "Miami Grand Prix", "Miami", "USA"),
    "montreal": ("villeneuve", "Canadian Grand Prix", "Montreal", "Canada"),
    "shanghai": ("shanghai", "Chinese Grand Prix", "Shanghai", "China"),
    "baku": ("baku", "Azerbaijan Grand Prix", "Baku", "Azerbaijan"),
    "catalunya": ("catalunya", "Spanish Grand Prix", "Barcelona", "Spain"),
    "hockenheim": ("hockenheim", "German Grand Prix", "Hockenheim", "Germany"),
    "hungaroring": ("hungaroring", "Hungarian Grand Prix", "Budapest", "Hungary"),
    "imola": ("imola", "Emilia Romagna Grand Prix", "Imola", "Italy"),
    "istanbul-park": ("istanbul", "Turkish Grand Prix", "Istanbul", "Turkey"),
    "jeddah": ("jeddah", "Saudi Arabian Grand Prix", "Jeddah", "Saudi Arabia"),
    "jerez": ("jerez", "European Grand Prix", "Jerez", "Spain"),
    "losail": ("losail", "Qatar Grand Prix", "Lusail", "Qatar"),
    "magny-cours": ("magny_cours", "French Grand Prix", "Magny-Cours", "France"),
    "marina-bay": ("marina_bay", "Singapore Grand Prix", "Marina Bay", "Singapore"),
    "monte-carlo": ("monaco", "Monaco Grand Prix", "Monte Carlo", "Monaco"),
    "monza": ("monza", "Italian Grand Prix", "Monza", "Italy"),
    "mugello": ("mugello", "Tuscan Grand Prix", "Mugello", "Italy"),
    "nurburgring": ("nurburgring", "European Grand Prix", "Nürburg", "Germany"),
    "paul-ricard": ("ricard", "French Grand Prix", "Le Castellet", "France"),
    "portimao": ("portimao", "Portuguese Grand Prix", "Portimão", "Portugal"),
    "sakhir": ("bahrain", "Bahrain Grand Prix", "Sakhir", "Bahrain"),
    "sepang": ("sepang", "Malaysian Grand Prix", "Kuala Lumpur", "Malaysia"),
    "silverstone": ("silverstone", "British Grand Prix", "Silverstone", "UK"),
    "sochi": ("sochi", "Russian Grand Prix", "Sochi", "Russia"),
    "spa-francorchamps": ("spa", "Belgian Grand Prix", "Spa-Francorchamps", "Belgium"),
    "spielberg": ("red_bull_ring", "Austrian Grand Prix", "Spielberg", "Austria"),
    "valencia": ("valencia", "European Grand Prix", "Valencia", "Spain"),
    "valencia-street-circuit": ("valencia", "European Grand Prix", "Valencia", "Spain"),
    "yas-marina": ("yas_marina", "Abu Dhabi Grand Prix", "Abu Dhabi", "UAE"),
    "zandvoort": ("zandvoort", "Dutch Grand Prix", "Zandvoort", "Netherlands"),
}


def circuit_of(url):
    s = url_slug(url, "circuits")
    if s not in CIRC:
        raise KeyError(f"unknown motorsportmag circuit slug {s!r} ({url}); add it to CIRC")
    return CIRC[s]


def season_raw(slug_name, refresh=False):
    """Every race of one championship season, chronological, with results/quali/box.
    refresh=True refetches it all (the live season gains races and corrections)."""
    if refresh:
        REFRESH.add(f"/championships/{slug_name}/")
    cid = champ_id(slug_name)
    if refresh:
        REFRESH.add(f"championship_id={cid}")
    out = []
    for rc in season_races(cid):
        if refresh:
            REFRESH.add(rc["url"])
        rid = post_id(rc["url"])
        if refresh:
            REFRESH.add(f"race_id={rid}")
        out.append({**rc, "rid": rid, "box": race_box(rid), "res": race_results(rid),
                    "quali": race_quali(rid), "date": pdate(rc.get("race_date")),
                    "laps_n": int(rc["laps"]) if str(rc.get("laps", "")).isdigit() else 0})
    out.sort(key=lambda r: (r["date"] or datetime.min, int(r["round"])))
    return cid, out, season_standings(cid)
