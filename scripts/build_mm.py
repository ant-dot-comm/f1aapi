#!/usr/bin/env python3
"""
build_mm.py — build every season of this series from motorsportmagazine.com.

    python scripts/build_mm.py              # all seasons (refetches the live season)
    python scripts/build_mm.py --cached     # don't refetch the live season (cache only)
    python scripts/build_mm.py --push       # ...then git commit + push

Per season it writes
    races/<y>/results.json        weekends: Sessions (type/label/order), Results, Bonuses
    races/<y>/resullts.json       same content under the original (misspelled) name
    races/<y>/calendar.json       rounds incl. scheduled ones of the live season
    standings/<y>/drivers.json    championship table with display fields + byRound
    standings/<y>/teams.json
    <ROSTER>/<y>/registry.json    who drove which car number for which team, per round
    <ROSTER>/<y>/drivers.json     legacy number -> {Driver, Constructor} roster
and series-wide seasons.json, drivers/index.json, teams/index.json, races/racesbyMK.json.

Nothing is computed on the frontend: points come straight from motorsportmag rows,
and a finished season must add up (race points + Bonuses) to motorsportmag's own
championship table, or the build stops before writing anything. Run stats.py after.

Identical in f2api and f1a-api (keep in sync); series rules live in series_config.py
and per-driver name/code fixes in people_overrides.json.
"""
import argparse
import collections
import glob
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import common as C
import motorsportmag as M
import series_config as S

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
OVERRIDES = json.load(open(os.path.join(HERE, "people_overrides.json")))
PARTICLES = {"de", "van", "der", "den", "da", "das", "dos", "di", "von", "le", "la", "del", "du", "al", "el"}
SUFFIXES = {"jr", "jr.", "jnr", "sr", "sr.", "ii", "iii", "iv"}
SUFFIX_SLUGS = {"jr", "jnr", "sr", "ii", "iii", "iv"}


class BuildError(Exception):
    pass


def num(v):
    try:
        return float(str(v).strip() or 0)
    except ValueError:
        return 0.0


def pstr(f):
    f = round(float(f), 2)
    return str(int(f)) if f == int(f) else str(f)


def iso(d):
    return d.strftime("%Y-%m-%d") if d else ""


# --------------------------------------------------------------------------- #
# identities
# --------------------------------------------------------------------------- #
def _in_range(year, spec):
    if not spec:
        return True
    a, _, b = spec.partition("-")
    return int(a) <= year <= int(b or a)


def team_id(team_url, year):
    """motorsportmag team URL -> canonical constructorId via the lineage table."""
    s = M.url_slug(team_url, "teams")
    for t in S.TEAMS:
        for alias in t["mm"]:
            name, _, yrs = alias.partition("@")
            if name == s and _in_range(year, yrs):
                return t["id"]
    raise BuildError(f"{year}: motorsportmag team {s!r} is not in series_config.TEAMS")


TEAM_META = {t["id"]: t for t in S.TEAMS}


def split_name(full):
    """'Giedo van der Garde' -> ('Giedo', 'van der Garde'); 'Nelson Piquet Jr' -> ('Nelson', 'Piquet Jr')."""
    p = full.split()
    if len(p) < 2:
        return "", full
    end = len(p) - 1
    suffix = ""
    if C.slug(p[end]) in SUFFIX_SLUGS and end > 1:
        suffix = " " + p[end]
        end -= 1
    start = end
    while start - 1 > 0 and p[start - 1].lower() in PARTICLES:
        start -= 1
    return " ".join(p[:start]), " ".join(p[start:end + 1]) + suffix


def build_people(raws):
    """One record per real person (motorsportmag driver URL), with a stable driverId."""
    people = {}
    for year, (_, races, standings) in raws.items():
        for rc in races:
            for r in rc["res"]:
                s = M.url_slug(r.get("driver_url"), "drivers")
                if not s:
                    raise BuildError(f"{year}: result row without driver_url: {r.get('driver_name')}")
                people.setdefault(s, {"mm": s, "mmName": r.get("driver_name", ""), "seasons": set()})["seasons"].add(year)
            for q in rc["quali"] if isinstance(rc["quali"], list) else []:
                s = M.url_slug(q.get("driver_url"), "drivers")
                if s and s not in people:
                    people[s] = {"mm": s, "mmName": q.get("driver_name", ""), "seasons": {year}}
    with ThreadPoolExecutor(8) as ex:
        for s, bio in zip(list(people), ex.map(M.driver_box, list(people))):
            people[s].update(bio)
    for s, p in people.items():
        o = OVERRIDES.get(s, {})
        given, family = split_name(p["mmName"])
        p["givenName"] = o.get("givenName", given)
        p["familyName"] = o.get("familyName", family)
        p["name"] = o.get("name") or f"{p['givenName']} {p['familyName']}".strip()
        p["_base"] = C.slug(" ".join(w for w in p["familyName"].split() if C.slug(w) not in SUFFIX_SLUGS))
    # surname collisions between different people -> qualify every one of them with the first name
    by_base = collections.defaultdict(list)
    for p in people.values():
        by_base[p["_base"]].append(p)
    for base, group in by_base.items():
        for p in group:
            fixed = OVERRIDES.get(p["mm"], {}).get("driverId")
            if fixed:
                p["driverId"] = fixed
            elif len(group) > 1:
                p["driverId"] = f"{C.slug(p['givenName'].split()[0])}_{base}"
            else:
                p["driverId"] = base
    ids = collections.Counter(p["driverId"] for p in people.values())
    dup = [i for i, n in ids.items() if n > 1]
    if dup:
        raise BuildError(f"driverId collision after disambiguation: {dup}")
    return people


def code_for(p, year, taken):
    """Three-letter code: the override (official / matches the portrait files), else generated."""
    o = OVERRIDES.get(p["mm"], {})
    code = (o.get("codes") or {}).get(str(year)) or o.get("code")
    if code:
        return code, "official"
    letters = "".join(ch for ch in C.deaccent(p["familyName"]).upper() if ch.isalpha())
    given = "".join(ch for ch in C.deaccent(p["givenName"]).upper() if ch.isalpha()) or "X"
    for cand in (letters[:3], given[:1] + letters[:2], letters[:2] + given[:1], given[:2] + letters[:1]):
        if len(cand) == 3 and cand not in taken:
            return cand, "generated"
    return (letters + "XXX")[:3], "generated"


# --------------------------------------------------------------------------- #
# sessions
# --------------------------------------------------------------------------- #
def classify(rc, year):
    """Sprint or feature, from the points scale the race actually paid (bonuses touch one
    or two rows; half points show up as the whole scale halved)."""
    rows = sorted((r for r in rc["res"] if str(r.get("position", "")).strip().isdigit()),
                  key=lambda r: int(r["position"]))
    pts = [num(r.get("points")) for r in rows]
    if not any(pts):
        return None, False                    # nothing paid (e.g. stopped before points were due)
    best = None
    for typ, scale in S.scales(year).items():
        for mult in (1.0, 0.5):
            hits = sum(1 for i, p in enumerate(scale) if i < len(pts) and abs(pts[i] - p * mult) < 1e-6)
            key = (hits, mult == 1.0, typ == "feature")
            if best is None or key > best[0]:
                best = (key, typ, mult)
    (hits, _, _), typ, mult = best
    if hits < 3:
        # reshuffled after penalties: the race that paid more at the top is the feature
        sc = S.scales(year)
        typ = "feature" if max(pts) > (sc["feature"][0] + sc["sprint"][0]) / 2 else "sprint"
        print(f"  WARN {year} {rc.get('name')} ({rc.get('race_date')}): points {pts[:10]} fit no scale; typed {typ} by top score")
        return typ, False
    return typ, mult == 0.5


def group_weekends(races):
    weekends, cur = [], []
    for rc in races:
        if cur and (
            (rc["date"] and cur[-1]["date"] and (rc["date"] - cur[-1]["date"]).days > 3)
            or M.url_slug(rc.get("circuit_url"), "circuits") != M.url_slug(cur[-1].get("circuit_url"), "circuits")
        ):
            weekends.append(cur)
            cur = []
        cur.append(rc)
    if cur:
        weekends.append(cur)
    return weekends


STATUS = {"DNS": "DNS", "DSQ": "DSQ", "DQ": "DSQ", "EX": "DSQ", "NC": "NC", "DNQ": "DNQ"}


def rows_for(rc, year, rno, people, roster, is_feature):
    box = rc["box"] or {}
    fl = box.get("fastest_lap") or {}
    fl_key = C.slug((fl.get("name") or "").split()[-1]) if fl.get("name") else None
    fl_hits = [r for r in rc["res"] if fl_key and C.slug(r.get("driver_name", "").split()[-1]) == fl_key]
    fl_url = fl_hits[0].get("driver_url") if len(fl_hits) == 1 else None
    grid_q = {}
    if is_feature and isinstance(rc["quali"], list):
        grid_q = {str(q.get("driver_number")): str(q.get("grid")) for q in rc["quali"]}
    win_t = None
    for r in rc["res"]:
        t = M.t2s(r.get("race_time", ""))
        if win_t is None and t is not None and str(r.get("position", "")).strip().isdigit():
            win_t = t
    out = []
    for i, r in enumerate(rc["res"]):
        p = people[M.url_slug(r.get("driver_url"), "drivers")]
        cid = team_id(r.get("team_url"), year)
        nbr = str(r.get("driver_number") or "")
        pos = str(r.get("position") or "").strip()
        fin = pos.isdigit()
        rtime = (r.get("race_time") or "").strip()
        t = M.t2s(rtime)
        mv = r.get("movement")
        grid = ""
        if fin and isinstance(mv, int):
            grid = str(int(pos) + mv)
        elif nbr in grid_q:
            grid = grid_q[nbr]
        if fin:
            gap = "-" if i == 0 or win_t is None or t is None else (M.fmt_gap(t - win_t) if t >= win_t else rtime)
            status = "Finished"
        else:
            gap = rtime or pos
            status = STATUS.get(pos.upper(), "DNF")
        roster.setdefault(p["driverId"], {}).setdefault((nbr, cid), set()).add(rno)
        out.append({
            "number": nbr, "driverId": p["driverId"], "constructorId": cid, "grid": grid,
            "position": pos if fin else "NC",
            "laps": str(rc["laps_n"]) if fin else "",
            "gap": gap, "status": status,
            "points": pstr(num(r.get("points"))),
            "Time": {"time": rtime},
            "FastestLap": {"lap": "0", "Time": {"time": M.fmt_lap(fl.get("time", "")) if r.get("driver_url") == fl_url else ""}},
        })
    fastest = None
    if fl_url:
        fastest = {"driverId": people[M.url_slug(fl_url, "drivers")]["driverId"], "time": M.fmt_lap(fl.get("time", ""))}
    pole = None
    poles = box.get("race_poles") or []
    if is_feature and poles and M.url_slug(poles[0].get("driver_url"), "drivers") in people:
        pole = people[M.url_slug(poles[0]["driver_url"], "drivers")]["driverId"]
    return out, fastest, pole


def qualifying_for(rc, year, people):
    """The qualifying session that set this race's grid, or None (a reverse grid has no lap
    times). motorsportmag lists the starting grid (after penalties) with each driver's lap:
    `grid` keeps that order, `position` is the classification by lap time. When lap times
    can't reproduce the order (groups at Monaco, deleted laps, red flags), the grid order
    stands and the session is marked order: "grid"."""
    rows = rc["quali"] if isinstance(rc["quali"], list) else []
    if not any(M.t2s(q.get("qualifying_time")) for q in rows):
        return None
    rows = sorted(rows, key=lambda q: int(q["grid"]) if str(q.get("grid", "")).isdigit() else 999)
    timed = sorted((q for q in rows if M.t2s(q.get("qualifying_time"))), key=lambda q: M.t2s(q["qualifying_time"]))
    by_time = {id(q): i for i, q in enumerate(timed, 1)}
    displaced = sum(1 for i, q in enumerate(rows, 1) if id(q) in by_time and abs(by_time[id(q)] - i) >= 2)
    by_grid = displaced > 0.3 * len(timed)
    order = rows if by_grid else timed + [q for q in rows if id(q) not in by_time]
    out = []
    for pos, q in enumerate(order, 1):
        out.append({"position": str(pos), "driverId": people[M.url_slug(q.get("driver_url"), "drivers")]["driverId"],
                    "constructorId": team_id(q.get("team_url"), year), "number": str(q.get("driver_number") or ""),
                    "time": M.fmt_lap(q.get("qualifying_time", "")), "grid": str(q.get("grid") or "")})
    session = {"Results": out}
    if by_grid:
        session["order"] = "grid"
    return session


def apply_corrections(year, rno, rtype, idx, rows, people):
    """Official post-race changes motorsportmag has not picked up yet (series_config.CORRECTIONS)."""
    for c in S.CORRECTIONS:
        if (c["season"], c["round"], c["type"], c.get("index", 1)) != (year, rno, rtype, idx):
            continue
        changed = False
        for slug_, fix in c["set"].items():
            did = people[slug_]["driverId"]
            row = next(r for r in rows if r["driverId"] == did)
            if (row["position"], row["points"]) != (str(fix["position"]), pstr(fix["points"])):
                row["position"], row["points"] = str(fix["position"]), pstr(fix["points"])
                changed = True
        if not changed:
            print(f"  NOTE {year} round {rno} {rtype}: motorsportmag now matches CORRECTIONS ({c['note']}); the entry can go")
        rows.sort(key=lambda r: int(r["position"]) if r["position"].isdigit() else 999)
    return rows


def build_weekends(year, races, people):
    weekends = group_weekends(races)
    # rounds motorsportmag has no race for (every session cancelled) keep their place in the calendar
    for e in sorted((e for e in S.EMPTY_ROUNDS if e["season"] == year), key=lambda e: e["round"]):
        weekends.insert(e["round"] - 1, e)
    roster = {}                               # driverId -> {(number, constructorId): {round}}
    rounds = []
    visits = collections.Counter()
    for wi, wk in enumerate(weekends, 1):
        rno = str(wi)
        empty = isinstance(wk, dict)
        typed = []
        for order, rc in enumerate([] if empty else wk, 1):
            typ, half = classify(rc, year)
            typed.append({"rc": rc, "type": typ, "half": half, "order": order, "noPoints": typ is None})
        for s in typed:
            if s["type"] is None:              # unpaid race: the shorter one of the weekend is the sprint
                longest = max(x["rc"]["laps_n"] for x in typed)
                s["type"] = "sprint" if s["rc"]["laps_n"] < longest or len(typed) == 1 else "feature"
        # sessions that were scheduled but not run slot in at their running order
        for c in sorted((c for c in S.CANCELLED if c["season"] == year and c["round"] == wi), key=lambda c: c["order"]):
            typed.insert(c["order"] - 1, {"type": c["type"], "cancelled": c["note"]})
        for order, s in enumerate(typed, 1):
            s["order"] = order
        ran = [s for s in typed if "cancelled" not in s]
        for s, key in zip(ran, S.legacy_keys(year, ran)):
            s["key"] = key
        sessions, results, qualifying = [], {}, []
        for s in typed:
            n_type = sum(1 for x in typed if x["type"] == s["type"])
            idx = [x for x in typed if x["type"] == s["type"]].index(s) + 1
            label = S.session_label(year, s["type"], idx, n_type, s["order"], len(typed))
            if "cancelled" in s:
                sessions.append({"key": None, "type": s["type"], "label": label, "order": s["order"],
                                 "cancelled": True, "note": s["cancelled"]})
                continue
            rows, fastest, pole = rows_for(s["rc"], year, wi, people, roster, s["type"] == "feature")
            rows = apply_corrections(year, wi, s["type"], idx, rows, people)
            sess = {"key": s["key"], "type": s["type"], "label": label,
                    "order": s["order"], "date": iso(s["rc"]["date"]), "laps": s["rc"]["laps_n"]}
            if s["half"]:
                sess["halfPoints"] = True
            if s.get("noPoints"):
                sess["noPoints"] = True
            if pole:
                sess["pole"] = pole
            if fastest:
                sess["fastestLap"] = fastest
            sessions.append(sess)
            results[s["key"]] = rows
            q = qualifying_for(s["rc"], year, people)
            if q:
                qualifying.append({"race": s["key"], **q})
        if empty:
            circ_id, race_name, locality, country = M.CIRC[wk["mm"]]
            circuit_name, date = wk["circuitName"], wk["date"]
        else:
            circ_id, race_name, locality, country = M.circuit_of(wk[-1].get("circuit_url"))
            circuit_name, date = wk[-1].get("circuit_name", ""), iso(wk[-1]["date"])
        visits[circ_id] += 1
        race_name = S.race_name(year, circ_id, race_name, visits[circ_id])
        wk_out = {"season": str(year), "round": rno, "raceName": race_name, "date": date,
                  "Circuit": {"circuitId": circ_id, "circuitName": circuit_name,
                              "Location": {"locality": locality, "country": country}},
                  "Sessions": sessions,
                  "Results": {k: results[k] for k in sorted(results)}}
        if qualifying:
            wk_out["Qualifying"] = [{"race": q["race"], "label": S.qualifying_label(year, i, len(qualifying)), **q}
                                    for i, q in enumerate(qualifying, 1)]
        if empty:
            wk_out["note"] = wk["note"]
        rounds.append(wk_out)
    for b in S.BONUSES:
        if b["season"] != year:
            continue
        p = people[b["driver"]]
        wi = b["round"]
        # the team the driver raced for that round (or the nearest round, when nothing ran)
        ents = roster[p["driverId"]].items()
        cid = min(ents, key=lambda kv: min(abs(r - wi) for r in kv[1]))[0][1]
        rounds[wi - 1].setdefault("Bonuses", []).append(
            {"driverId": p["driverId"], "constructorId": cid, "type": b["type"],
             "points": pstr(b["points"]), "note": b["note"]})
    return rounds, roster


# --------------------------------------------------------------------------- #
# standings
# --------------------------------------------------------------------------- #
def tally(rounds, key, exclude=lambda row, rno: False):
    """Per entity (driverId / constructorId): points by round and finishing positions."""
    pts = collections.defaultdict(lambda: collections.defaultdict(float))
    places = collections.defaultdict(list)
    wins = collections.Counter()
    for rd in rounds:
        rno = int(rd["round"])
        for rows in rd["Results"].values():
            for row in rows:
                if exclude(row, rno):
                    continue
                ent = row[key]
                pts[ent][rno] += num(row["points"])
                if row["position"].isdigit():
                    places[ent].append(int(row["position"]))
                    if row["position"] == "1":
                        wins[ent] += 1
        for b in rd.get("Bonuses", []):
            if not exclude(b, rno) and b.get(key):
                pts[b[key]][rno] += num(b["points"])
    return pts, places, wins


def countback(places):
    c = collections.Counter(places)
    return tuple(c.get(i, 0) for i in range(1, 41))


def ranked(entities, totals, places):
    # points, then countback; the id only makes a dead heat come out the same on every build
    return sorted(entities, key=lambda e: (-round(totals[e], 2), tuple(-x for x in countback(places[e])), e))


def by_round(order_fn, pts, entities, n_rounds):
    """[{round, points, total, position}] per entity, position = championship place after the round."""
    out = {e: [] for e in entities}
    running = collections.defaultdict(float)
    for rno in range(1, n_rounds + 1):
        for e in entities:
            running[e] += pts[e].get(rno, 0.0)
        order = order_fn(dict(running), rno)
        for pos, e in enumerate(order, 1):
            if e in out:
                out[e].append({"round": str(rno), "points": pstr(pts[e].get(rno, 0.0)),
                               "total": pstr(running[e]), "position": str(pos)})
    return out


def build_standings(year, rounds, roster, people_by_id, mm_standings, complete, codes, wildcards):
    n = len(rounds)
    pts, places, wins = tally(rounds, "driverId")
    totals = {d: sum(v.values()) for d, v in pts.items()}
    drivers = set(pts) | {d for d in roster}
    # mid-round places for byRound countback
    places_upto = lambda rno: tally(rounds[:rno], "driverId")[1]

    def order_after(running, rno):
        pl = places_upto(rno)
        return ranked(list(running), running, pl)

    bad, corrected = [], False
    if mm_standings:
        url_to_id = {p["mm"]: p["driverId"] for p in people_by_id.values()}
        official = []
        for st in sorted(mm_standings, key=lambda s: int(s["standing"])):
            did = url_to_id.get(M.url_slug(st.get("driver_url"), "drivers"))
            if not did:
                raise BuildError(f"{year}: standings driver {st.get('driver_name')} has no results")
            official.append((did, num(st["points"])))
        off = [(d, o, round(totals.get(d, 0), 2)) for d, o in official if abs(o - totals.get(d, 0)) > 1e-6]
        accepted = {(a["season"], a["driverId"]) for a in S.ACCEPT_DIFFS}
        bad = [x for x in off if (year, x[0]) not in accepted]
        corrected = any(c["season"] == year for c in S.CORRECTIONS)
        if bad and not corrected:
            raise BuildError(f"{year}: race points + Bonuses differ from motorsportmag standings: "
                             + ", ".join(f"{d} official={o:g} summed={g:g}" for d, o, g in bad))
        if bad:
            print(f"  NOTE {year}: standings from corrected race rows; motorsportmag's table differs for "
                  + ", ".join(f"{d} ({o:g} -> {g:g})" for d, o, g in bad))
    if mm_standings and not (bad and corrected):
        order = [d for d, _ in official] + ranked([d for d in drivers if d not in dict(official)], totals, places)
        points_of = dict(official)
    else:
        order = ranked(list(drivers), totals, places)
        points_of = {d: totals.get(d, 0.0) for d in drivers}
    br = by_round(order_after, pts, list(drivers), n)
    rows = []
    for pos, d in enumerate(order, 1):
        p = people_by_id[d]
        teams_in_order = [c for (nbr, c), rs in sorted(roster.get(d, {}).items(), key=lambda kv: min(kv[1]))]
        teams = list(dict.fromkeys(teams_in_order))
        last = max(roster.get(d, {}).items(), key=lambda kv: max(kv[1]))[0][1] if roster.get(d) else None
        row = {"position": str(pos), "driverId": d, "code": codes[d], "driver": p["name"],
               "givenName": p["givenName"], "familyName": p["familyName"],
               "nationality": p.get("nationality", ""), "constructorId": last,
               "points": pstr(points_of.get(d, 0.0)), "wins": str(wins.get(d, 0))}
        if len(teams) > 1:
            row["teams"] = teams
        if d in wildcards:
            row["wildcard"] = True
        row["byRound"] = br[d]
        rows.append(row)

    # teams: every car's points for the team it raced for (wildcards excluded where the series says so)
    def excl(row, rno):
        return (not S.wildcards_score_for_team(year)) and row["driverId"] in wildcards

    tpts, tplaces, twins = tally(rounds, "constructorId", excl)
    ttot = {c: sum(v.values()) for c, v in tpts.items()}
    teams = set(tpts) | {c for ents in roster.values() for (_, c) in ents}
    torder = ranked(list(teams), ttot, tplaces)
    tplaces_upto = lambda rno: tally(rounds[:rno], "constructorId", excl)[1]
    tbr = by_round(lambda running, rno: ranked(list(running), running, tplaces_upto(rno)), tpts, list(teams), n)
    names = team_names_for(year, rounds)
    team_rows = [{"position": str(i), "constructorId": c, "team": names.get(c, TEAM_META[c]["name"]),
                  "points": pstr(ttot.get(c, 0.0)), "wins": str(twins.get(c, 0)), "byRound": tbr[c]}
                 for i, c in enumerate(torder, 1)]
    return rows, team_rows


TEAM_NAMES = {}   # (year, constructorId) -> Counter of motorsportmag entrant names


def team_names_for(year, rounds):
    return {c: cnt.most_common(1)[0][0] for (y, c), cnt in TEAM_NAMES.items() if y == year}


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def build(cached=False):
    raws = {}
    live = S.LIVE_SEASON
    for year in S.YEARS:
        raws[year] = M.season_raw(S.champ_slug(year), refresh=(year == live and not cached))
        if not raws[year][1]:
            raise BuildError(f"{year}: motorsportmag returned no races for {S.champ_slug(year)}")
    for year, (_, races, _) in raws.items():
        for rc in races:
            for r in rc["res"]:
                TEAM_NAMES.setdefault((year, team_id(r.get("team_url"), year)), collections.Counter())[r.get("team_name") or ""] += 1
    people = build_people(raws)
    by_id = {p["driverId"]: p for p in people.values()}
    files = {}            # path -> object (written only after every season passes)
    seasons_out, calendars = [], {}
    code_seasons = collections.defaultdict(dict)
    team_seasons = collections.defaultdict(set)

    for year in S.YEARS:
        cid, races, mm_st = raws[year]
        rounds, roster = build_weekends(year, races, people)
        scheduled = S.SCHEDULED_ROUNDS.get(year, len(rounds))
        complete = bool(mm_st) and len(rounds) >= scheduled
        if year != live and not complete:
            raise BuildError(f"{year}: only {len(rounds)}/{scheduled} rounds or no standings on motorsportmag")
        wildcards = {people[s]["driverId"] for s, o in OVERRIDES.items()
                     if year in o.get("wildcard", []) and s in people}
        taken, codes, code_src = set(), {}, {}
        for d in sorted(roster, key=lambda d: (not OVERRIDES.get(by_id[d]["mm"], {}).get("code"), d)):
            codes[d], code_src[d] = code_for(by_id[d], year, taken)
            taken.add(codes[d])
            code_seasons[d][str(year)] = (codes[d], code_src[d])
        drv_rows, team_rows = build_standings(year, rounds, roster, by_id, mm_st, complete, codes, wildcards)
        meta = {"season": str(year), "series": S.era_of(year), "championship": S.champ_name(year),
                "source": "motorsportmagazine.com", "updated": C.today_iso(),
                "lastRound": len(rounds), "scheduledRounds": scheduled, "complete": complete}
        files[f"standings/{year}/drivers.json"] = {**meta, "Standings": drv_rows}
        files[f"standings/{year}/teams.json"] = {**meta, "Standings": team_rows}
        files[f"races/{year}/results.json"] = rounds
        files[f"races/{year}/resullts.json"] = rounds

        cal = [{"round": rd["round"], "raceId": f"{year}{rd['round']}", "raceName": rd["raceName"],
                "circuitId": rd["Circuit"]["circuitId"], "circuitName": rd["Circuit"]["circuitName"],
                "locality": rd["Circuit"]["Location"]["locality"], "country": rd["Circuit"]["Location"]["country"],
                "date": rd["date"], "status": "complete"} for rd in rounds]
        for fut in S.FUTURE_ROUNDS.get(year, []):
            if int(fut["round"]) > len(rounds):
                cid_f, name_f, loc_f, country_f = M.CIRC[fut["mm"]]
                cal.append({"round": str(fut["round"]), "raceId": f"{year}{fut['round']}", "raceName": name_f,
                            "circuitId": cid_f, "circuitName": fut.get("circuitName", ""), "locality": loc_f,
                            "country": country_f, "date": fut.get("date", ""), "status": "scheduled"})
        calendars[year] = cal
        files[f"races/{year}/calendar.json"] = {"season": str(year), "series": S.era_of(year), "Races": cal}

        reg = {}
        for d, ents in sorted(roster.items()):
            p = by_id[d]
            rec = {"driverId": d, "name": p["name"], "givenName": p["givenName"], "familyName": p["familyName"],
                   "code": codes[d], "entries": [{"number": n_, "constructorId": c, "rounds": [str(r) for r in sorted(rs)]}
                                                 for (n_, c), rs in sorted(ents.items(), key=lambda kv: min(kv[1]))]}
            if d in wildcards:
                rec["wildcard"] = True
            reg[d] = rec
            for (_, c) in ents:
                team_seasons[c].add(year)
        files[f"{S.ROSTER_DIR}/{year}/registry.json"] = {"season": str(year), "series": S.era_of(year),
                                                         "championship": S.champ_name(year), "Drivers": reg}
        # legacy roster keyed by car number (Driver, Driver2, ... in the order they used it)
        legacy = {}
        by_num = collections.defaultdict(list)
        for d, ents in roster.items():
            for (n_, c), rs in ents.items():
                by_num[n_].append((min(rs), d, c))
        for n_, lst in sorted(by_num.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 999):
            lst.sort()
            ent = {}
            for i, (_, d, c) in enumerate(lst):
                p = by_id[d]
                ent["Driver" if i == 0 else f"Driver{i + 1}"] = {
                    "driverId": d, "permanentNumber": n_, "code": codes[d],
                    "givenName": p["givenName"], "familyName": p["familyName"]}
            last_team = lst[-1][2]
            ent["Constructor"] = {"constructorId": last_team, "name": TEAM_META[last_team]["shortName"]}
            legacy[n_] = ent
        files[f"{S.ROSTER_DIR}/{year}/drivers.json"] = legacy
        if S.LEGACY_TEAM_ROSTERS:
            per_team = collections.defaultdict(list)
            for n_, ent in legacy.items():
                for k, v in ent.items():
                    if k.startswith("Driver"):
                        team_of = next(c for (nn, c) in roster[v["driverId"]] if nn == n_)
                        per_team[team_of].append(v)
            for c, lst in per_team.items():
                files[f"{S.ROSTER_DIR}/{year}/{c}.json"] = lst

        champ = drv_rows[0] if drv_rows and complete else None
        tchamp = team_rows[0] if team_rows and complete else None
        seasons_out.append({"season": str(year), "series": S.era_of(year), "championship": S.champ_name(year),
                            "rounds": len(rounds), "scheduledRounds": scheduled, "complete": complete,
                            "champion": {"driverId": champ["driverId"], "name": champ["driver"]} if champ else None,
                            "teamChampion": {"constructorId": tchamp["constructorId"], "name": tchamp["team"]} if tchamp else None})
        print(f"{year} [{S.era_of(year)}] {len(rounds)}/{scheduled} rounds, {len(roster)} drivers, "
              f"{len(team_rows)} teams, {'complete' if complete else 'IN PROGRESS'}"
              + (f", champion {champ['driver']} {champ['points']}" if champ else f", leader {drv_rows[0]['driver']} {drv_rows[0]['points']}"))

    files["seasons.json"] = {"series": S.SERIES, "updated": C.today_iso(), "seasons": seasons_out}
    drivers_idx = []
    for d, p in sorted(by_id.items(), key=lambda kv: (C.deaccent(kv[1]["familyName"]).lower(), kv[0])):
        cs = code_seasons[d]
        latest = cs[max(cs)] if cs else ("", "generated")
        drivers_idx.append({"driverId": d, "name": p["name"], "givenName": p["givenName"], "familyName": p["familyName"],
                            "code": latest[0], "codeSource": latest[1], "nationality": p.get("nationality", ""),
                            "countryCode": p.get("countryCode", ""), "dateOfBirth": p.get("dateOfBirth", ""),
                            "birthplace": p.get("birthplace", ""), "seasons": sorted(str(y) for y in p["seasons"]),
                            "motorsportmag": p["mm"]})
    files["drivers/index.json"] = {"series": S.SERIES, "updated": C.today_iso(), "drivers": drivers_idx}
    teams_idx = []
    for t in S.TEAMS:
        yrs = sorted(team_seasons.get(t["id"], []))
        if not yrs:
            continue
        names = {str(y): team_names_for(y, None).get(t["id"]) for y in yrs}
        teams_idx.append({"constructorId": t["id"], "name": t["name"], "shortName": t["shortName"],
                          "names": names, "seasons": [str(y) for y in yrs],
                          "predecessor": t.get("predecessor"), "successor": next((x["id"] for x in S.TEAMS if x.get("predecessor") == t["id"]), None)})
    files["teams/index.json"] = {"series": S.SERIES, "updated": C.today_iso(), "teams": teams_idx}
    files["races/racesbyMK.json"] = {e["raceId"]: {"circuitId": e["circuitId"], "raceName": e["raceName"],
                                                   "location": e["locality"], "year": str(y)}
                                     for y, cal in calendars.items() for e in cal}
    if S.LEGACY_RACES_JSON:
        files["races/races.json"] = {str(y): {e["raceName"]: {"meeting_key": int(e["raceId"]), "location": e["locality"]}
                                              for e in cal} for y, cal in calendars.items()}
    return files


def write(files):
    # generated per-season roster files that no longer exist (e.g. a renamed team) are removed
    for pattern in (f"{S.ROSTER_DIR}/*/*.json",):
        for p in glob.glob(os.path.join(REPO, pattern)):
            rel = os.path.relpath(p, REPO)
            if rel not in files:
                os.remove(p)
    for rel, obj in files.items():
        C.write_json(os.path.join(REPO, rel), obj)


def main():
    ap = argparse.ArgumentParser(description="Build the API from motorsportmagazine.com")
    ap.add_argument("--cached", action="store_true", help="don't refetch the live season")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    try:
        files = build(cached=a.cached)
    except BuildError as e:
        print(f"BUILD FAILED, nothing written: {e}")
        sys.exit(1)
    write(files)
    print(f"[{S.SERIES}] wrote {len(files)} files")
    if a.push:
        subprocess.run(["git", "-C", REPO, "add", "-A"], check=True)
        subprocess.run(["git", "-C", REPO, "commit", "-m", f"{S.SERIES}: rebuild from motorsportmagazine.com"], check=True)
        subprocess.run(["git", "-C", REPO, "push"], check=True)


if __name__ == "__main__":
    main()
