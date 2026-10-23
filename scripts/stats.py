#!/usr/bin/env python3
"""
stats.py — derive per-driver and per-team career/season statistics from the files
build_mm.py writes, so the frontend can build driver-comparison and team-history
pages without computing anything itself. Writes:

    stats/drivers/<driverId>.json    career + per-season metrics + every result
    stats/teams/<constructorId>.json team history + metrics + drivers + leaders
    stats/index.json                 driver + team lists (for comparison pickers)

Race types come from each weekend's Sessions (never from the race key), poles and
fastest laps from the session's `pole` / `fastestLap`, points from the rows plus the
weekend's Bonuses, season totals from the championship tables.
Identical in f2api and f1a-api (keep in sync). Run after build_mm.py.

    python scripts/stats.py            # write all
    python scripts/stats.py --push
"""
import argparse
import collections
import glob
import json
import os
import shutil

import common as C
import series_config as S

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEADER_KEYS = ("starts", "wins", "podiums", "poles", "points", "titles")


def _pts(v):
    """Points as a number; int when whole, float for half-points (e.g. 214.5)."""
    f = round(float(v or 0), 2)
    return int(f) if f == int(f) else f


def _load(rel):
    p = os.path.join(REPO, rel)
    return json.load(open(p)) if os.path.exists(p) else None


def _blank():
    return {"entries": 0, "starts": 0, "points": 0,
            "wins": {"total": 0}, "podiums": {"total": 0},
            "poles": 0, "fastestLaps": 0, "dnfs": 0, "pointsFinishes": 0,
            "bestFinish": None, "_fin": []}


def _add_result(s, rtype, row, pole, fastest):
    s["entries"] += 1
    if row["status"] not in ("DNS", "DNQ"):
        s["starts"] += 1
    if row["status"] == "DNF":
        s["dnfs"] += 1
    if float(row.get("points") or 0) > 0:
        s["pointsFinishes"] += 1
    pos = row["position"]
    if pos.isdigit():
        p = int(pos)
        s["_fin"].append(p)
        s["bestFinish"] = p if s["bestFinish"] is None else min(s["bestFinish"], p)
        if p == 1:
            s["wins"]["total"] += 1
            s["wins"][rtype] = s["wins"].get(rtype, 0) + 1
        if p <= 3:
            s["podiums"]["total"] += 1
            s["podiums"][rtype] = s["podiums"].get(rtype, 0) + 1
    if pole:
        s["poles"] += 1
    if fastest:
        s["fastestLaps"] += 1


def _finalize(s):
    fin = s.pop("_fin")
    s["avgFinish"] = round(sum(fin) / len(fin), 2) if fin else None
    return s


def _merge(into, src):
    for k in ("entries", "starts", "poles", "fastestLaps", "dnfs", "pointsFinishes"):
        into[k] += src[k]
    for grp in ("wins", "podiums"):
        for t, n in src[grp].items():
            into[grp][t] = into[grp].get(t, 0) + n
    into["_fin"].extend(src.get("_fin", []))
    if src.get("bestFinish") is not None:
        into["bestFinish"] = (src["bestFinish"] if into["bestFinish"] is None
                              else min(into["bestFinish"], src["bestFinish"]))


def build():
    seasons = _load("seasons.json")["seasons"]
    people = {d["driverId"]: d for d in _load("drivers/index.json")["drivers"]}
    team_meta = {t["constructorId"]: t for t in _load("teams/index.json")["teams"]}

    drv = {}    # driverId -> {"seasons": {yr: stats}, "results": [], "champ": {}, "points": {}, "teams": {yr: [cid]}}
    team = {}   # constructorId -> {"seasons": {yr: stats}, "champ": {}, "points": {}, "drivers": {did: stats}}
    dteam = {}  # (driverId, constructorId) -> per-driver stats while at that team
    titles = collections.defaultdict(list)       # constructorId -> [(year, driverId)]
    info = {s["season"]: s for s in seasons}

    for s in seasons:
        yr = s["season"]
        era, champ_name, complete = s["series"], s["championship"], s["complete"]
        for rnd in _load(f"races/{yr}/results.json") or []:
            sess = {x["key"]: x for x in rnd.get("Sessions", [])}
            for key, rows in rnd["Results"].items():
                meta = sess.get(key, {})
                rtype = meta.get("type", key)
                pole = meta.get("pole")
                fastest = (meta.get("fastestLap") or {}).get("driverId")
                for row in rows:
                    did, cid = row["driverId"], row.get("constructorId")
                    ds = drv.setdefault(did, {"seasons": {}, "results": [], "teams": {}})
                    is_pole, is_fl = pole == did, fastest == did
                    _add_result(ds["seasons"].setdefault(yr, _blank()), rtype, row, is_pole, is_fl)
                    if cid and cid not in ds["teams"].setdefault(yr, []):
                        ds["teams"][yr].append(cid)
                    ds["results"].append({
                        "season": yr, "round": rnd["round"], "raceName": rnd["raceName"],
                        "race": key, "type": rtype, "label": meta.get("label", key),
                        "constructorId": cid, "grid": row.get("grid", ""), "position": row["position"],
                        "status": row["status"], "points": row["points"]})
                    if cid:
                        ts = team.setdefault(cid, {"seasons": {}, "drivers": set()})
                        _add_result(ts["seasons"].setdefault(yr, _blank()), rtype, row, is_pole, is_fl)
                        ts["drivers"].add(did)
                        dt = dteam.setdefault((did, cid), {**_blank(), "seasons": set(), "teamPoints": 0.0})
                        _add_result(dt, rtype, row, is_pole, is_fl)
                        dt["seasons"].add(yr)
                        dt["teamPoints"] += float(row["points"] or 0)
            for b in rnd.get("Bonuses", []):
                if b.get("constructorId") and (b["driverId"], b["constructorId"]) in dteam:
                    dteam[(b["driverId"], b["constructorId"])]["teamPoints"] += float(b["points"])

        sd = _load(f"standings/{yr}/drivers.json")
        st = _load(f"standings/{yr}/teams.json")
        for e in sd["Standings"]:
            d = drv.setdefault(e["driverId"], {"seasons": {}, "results": [], "teams": {}})
            d.setdefault("champ", {})[yr] = int(e["position"])
            d.setdefault("points", {})[yr] = _pts(e["points"])
            if complete and e["position"] == "1":
                titles[e["constructorId"]].append((yr, e["driverId"]))
        for e in st["Standings"]:
            t = team.setdefault(e["constructorId"], {"seasons": {}, "drivers": set()})
            t.setdefault("champ", {})[yr] = int(e["position"])
            t.setdefault("points", {})[yr] = _pts(e["points"])

    for sub in ("drivers", "teams"):
        shutil.rmtree(os.path.join(REPO, "stats", sub), ignore_errors=True)
        os.makedirs(os.path.join(REPO, "stats", sub), exist_ok=True)

    d_index, t_index = [], []
    for did, ds in sorted(drv.items()):
        p = people.get(did, {})
        career, season_out = _blank(), {}
        for yr, s in sorted(ds["seasons"].items()):
            _merge(career, s)
            so = _finalize({**s, "_fin": list(s["_fin"])})
            so["points"] = ds.get("points", {}).get(yr, 0)
            so["championshipPosition"] = ds.get("champ", {}).get(yr)
            so["teams"] = ds["teams"].get(yr, [])
            so["complete"] = info[yr]["complete"]
            so["era"] = info[yr]["series"]
            so["championship"] = info[yr]["championship"]
            season_out[yr] = so
        career = _finalize(career)
        career["points"] = _pts(sum(ds.get("points", {}).values()))
        career["titles"] = len([y for y, pos in ds.get("champ", {}).items() if pos == 1 and info[y]["complete"]])
        allpos = list(ds.get("champ", {}).values())
        career["bestChampionship"] = min(allpos) if allpos else None
        career["eras"] = sorted({info[yr]["series"] for yr in ds["seasons"]})
        teams_all = list(dict.fromkeys(c for yr in sorted(ds["teams"]) for c in ds["teams"][yr]))
        doc = {"driverId": did, "name": p.get("name", did), "givenName": p.get("givenName", ""),
               "familyName": p.get("familyName", ""), "code": p.get("code", ""),
               "nationality": p.get("nationality", ""), "series": S.SERIES,
               "seasons": sorted(ds["seasons"]), "teams": teams_all,
               "career": career, "bySeasons": season_out, "results": ds["results"]}
        C.write_json(os.path.join(REPO, "stats", "drivers", f"{did}.json"), doc)
        d_index.append({"driverId": did, "name": doc["name"], "code": doc["code"],
                        "nationality": doc["nationality"], "seasons": doc["seasons"],
                        "eras": career["eras"], "titles": career["titles"], "points": career["points"]})

    for cid, ts in sorted(team.items()):
        tm = team_meta.get(cid, {})
        career, season_out = _blank(), {}
        for yr, s in sorted(ts["seasons"].items()):
            _merge(career, s)
            so = _finalize({**s, "_fin": list(s["_fin"])})
            so["points"] = ts.get("points", {}).get(yr, 0)
            so["championshipPosition"] = ts.get("champ", {}).get(yr)
            so["name"] = (tm.get("names") or {}).get(yr) or tm.get("name", cid)
            so["complete"] = info[yr]["complete"]
            so["era"] = info[yr]["series"]
            so["championship"] = info[yr]["championship"]
            season_out[yr] = so
        career = _finalize(career)
        career["points"] = _pts(sum(ts.get("points", {}).values()))
        career["titles"] = len([1 for y, pos in ts.get("champ", {}).items() if pos == 1 and info[y]["complete"]])
        career["driverTitles"] = len(titles.get(cid, []))
        allpos = list(ts.get("champ", {}).values())
        career["bestChampionship"] = min(allpos) if allpos else None
        career["eras"] = sorted({info[yr]["series"] for yr in ts["seasons"]})
        drivers = []
        won = collections.Counter(d for _, d in titles.get(cid, []))
        for did in ts["drivers"]:
            dt = dteam[(did, cid)]
            drivers.append({"driverId": did, "name": people.get(did, {}).get("name", did),
                            "code": people.get(did, {}).get("code", ""),
                            "seasons": sorted(dt["seasons"]), "starts": dt["starts"],
                            "wins": dt["wins"]["total"], "podiums": dt["podiums"]["total"],
                            "poles": dt["poles"], "fastestLaps": dt["fastestLaps"],
                            "points": _pts(dt["teamPoints"]), "titles": won.get(did, 0)})
        drivers.sort(key=lambda d: (-d["points"], -d["wins"], d["name"]))
        leaders = {}
        for k in LEADER_KEYS:
            ranked = sorted((d for d in drivers if d[k] > 0), key=lambda d: (-d[k], -d["points"], d["name"]))[:3]
            leaders[k] = [{"driverId": d["driverId"], "name": d["name"], "value": d[k]} for d in ranked]
        doc = {"constructorId": cid, "name": tm.get("name", cid), "shortName": tm.get("shortName", ""),
               "series": S.SERIES, "seasons": sorted(ts["seasons"]),
               "predecessor": tm.get("predecessor"), "successor": tm.get("successor"),
               "career": career, "bySeasons": season_out,
               "championDrivers": [{"season": y, "driverId": d, "name": people.get(d, {}).get("name", d)}
                                   for y, d in sorted(titles.get(cid, []))],
               "leaders": leaders, "drivers": drivers}
        C.write_json(os.path.join(REPO, "stats", "teams", f"{cid}.json"), doc)
        t_index.append({"constructorId": cid, "name": doc["name"], "seasons": doc["seasons"],
                        "eras": career["eras"], "titles": career["titles"], "points": career["points"]})

    d_index.sort(key=lambda x: -x["points"])
    t_index.sort(key=lambda x: -x["points"])
    C.write_json(os.path.join(REPO, "stats", "index.json"),
                 {"series": S.SERIES, "updated": C.today_iso(), "drivers": d_index, "teams": t_index})
    return len(d_index), len(t_index), [s["season"] for s in seasons]


def main():
    ap = argparse.ArgumentParser(description="Build derived driver/team stats")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    nd, nt, seasons = build()
    print(f"[{S.SERIES}] wrote stats for {nd} drivers, {nt} teams across seasons {seasons[0]}-{seasons[-1]}")
    if a.push:
        import subprocess
        subprocess.run(["git", "-C", REPO, "add", "stats"], check=True)
        subprocess.run(["git", "-C", REPO, "commit", "-m", f"Build {S.SERIES} stats layer"], check=True)
        subprocess.run(["git", "-C", REPO, "push"], check=True)


if __name__ == "__main__":
    main()
