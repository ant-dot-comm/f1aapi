#!/usr/bin/env python3
"""
standings_f1a.py — pull the official F1 Academy standings (driver + team) from
f1academy.com and write standings/<year>/{drivers,teams}.json.

f1academy.com server-renders standings as an HTML table: one <td> per event whose
`div.score` children hold the per-session points (2 = RG,FR; 3 = OR,RG,FR). Points
already include pole/FL bonuses, so we MIRROR them as the source of truth — this is
also where correct F1A points come from (the old computed POINTS tables were wrong:
makeup races can be feature-scale in an opening slot, and bonuses were missing).

Season switch is ?seasonId=N (2023=1 .. 2026=4). Works for all years.

    python scripts/standings_f1a.py --season 2026
    python scripts/standings_f1a.py --season 2026 --push
"""
import argparse
import os
import re
import sys

import common as C

BASE = "https://www.f1academy.com/Racing-Series/Standings/{kind}?seasonId={sid}"
SEASON_ID = {2023: 1, 2024: 2, 2025: 3, 2026: 4}
# Session->slot mapping differs by era. 2026+ labels columns OR/RG/FR (Opening/
# Reverse/Feature) -> race0/race1/race2. 2023-2025 label them R1/R2/R3 (chronological)
# -> race1/race2/race3. 2-race rounds are race1/race2 in both.
SLOTS_MODERN = {2: ["race1", "race2"], 3: ["race0", "race1", "race2"]}   # 2026+
SLOTS_LEGACY = {2: ["race1", "race2"], 3: ["race1", "race2", "race3"]}   # 2023-2025


def slots_for(season):
    return SLOTS_MODERN if int(season) >= 2026 else SLOTS_LEGACY


def _assign_ids(drivers):
    """Fill driverId. Roster matches are kept. For drivers with no roster id, use the
    surname slug — but if that surname is shared by >1 f1academy code (same-surname
    drivers, e.g. the Al Qubaisi sisters), use the unique code as the id instead."""
    from collections import defaultdict
    base = {id(e): (e["driverId"] or C.slug(C.surname_of(e["driver"]))) for e in drivers}
    codes = defaultdict(set)
    for e in drivers:
        codes[base[id(e)]].add(e["code"])
    for e in drivers:
        if e["driverId"] is None:
            e["driverId"] = C.slug(e["code"]) if len(codes[base[id(e)]]) > 1 else base[id(e)]
    return drivers


# Wildcards are detected from the "(WCD)" label f1academy puts in the driver name
# (authoritative); no hardcoded list needed.


def _scores(td):
    # the per-session cells have class exactly "score" (inside a "score-wrapper")
    return [C.cell_text(s) for s in td.xpath(
        './/*[contains(concat(" ", normalize-space(@class), " "), " score ")]')]


def parse(url, resolve, id_field, name_field, slots):
    table = C.fetch_html(url).xpath("//table")[0]
    out, unmatched, n_events = [], [], 0
    for tr in table.xpath(".//tbody/tr"):
        cells = tr.xpath("./th|./td")
        n_events = max(n_events, len(cells) - 2)   # minus the name + total columns
        pos = C.cell_text(cells[0].xpath('.//*[contains(@class,"pos")]')[0]).rstrip(".")
        code = C.cell_text(cells[0].xpath('.//*[contains(@class,"visible-desktop-down")]')[0])
        name = C.cell_text(cells[0].xpath('.//*[contains(@class,"visible-desktop-up")]')[0])
        # f1academy labels wildcard entries "(WCD)" in the name — authoritative.
        wildcard = bool(re.search(r"\(WC[D]?\)", name, re.I))
        name = re.sub(r"\s*\(WC[D]?\)\s*", "", name, flags=re.I).strip()
        total = C.cell_text(cells[1])
        by_round = []
        for rn, td in enumerate(cells[2:], start=1):
            sc = _scores(td)
            if not sc or all(v in ("-", "") for v in sc):
                continue
            slot_keys = slots.get(len(sc))
            if not slot_keys:
                continue
            rec = {"round": str(rn)}
            for slot, v in zip(slot_keys, sc):
                rec[slot] = v
            by_round.append(rec)
        _id = resolve(code)
        if _id is None:
            unmatched.append(f"{name} ({code})")
        entry = {"position": pos, id_field: _id, "code": code, name_field: name,
                 "points": total, "byRound": by_round}
        if id_field == "driverId":
            entry["wildcard"] = wildcard
        out.append(entry)
    return out, unmatched, n_events


def build(season):
    import json
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _rp = os.path.join(repo, "constructors", str(season), "drivers.json")
    roster = json.load(open(_rp)) if os.path.exists(_rp) else {}
    code2id = {i["Driver"]["code"]: i["Driver"]["driverId"] for i in roster.values()}
    teams = {i["Constructor"]["name"]: i["Constructor"]["constructorId"] for i in roster.values()}
    sid = SEASON_ID[season]

    def resolve_driver(code):
        return code2id.get(code)

    def resolve_team(name):
        for rname, cid in teams.items():          # roster name is a prefix of the full name
            if name.upper().startswith(rname.upper()):
                return cid
        return None

    slots = slots_for(season)
    drivers, d_un, scheduled = parse(BASE.format(kind="Driver", sid=sid), resolve_driver, "driverId", "driver", slots)
    tms, t_un, _ = parse(BASE.format(kind="Team", sid=sid), resolve_team, "constructorId", "team", slots)
    _assign_ids(drivers)                       # fills driverId incl. collision handling
    for e in tms:
        if e["constructorId"] is None:
            e["constructorId"] = C.slug(e["team"].split()[0])   # "PREMA Racing" -> prema

    last = max((int(r["round"]) for e in drivers for r in e["byRound"]), default=0)
    meta = {"season": str(season), "series": "f1a", "updated": C.today_iso(),
            "lastRound": last, "scheduledRounds": scheduled, "complete": last >= scheduled}
    dp = C.write_json(os.path.join(repo, "standings", str(season), "drivers.json"),
                      {**meta, "source": BASE.format(kind="Driver", sid=sid), "Standings": drivers})
    tp = C.write_json(os.path.join(repo, "standings", str(season), "teams.json"),
                      {**meta, "source": BASE.format(kind="Team", sid=sid), "Standings": tms})
    flagged = [f"{e['driver']} ({e['code']})" for e in drivers if e["wildcard"]]
    return dp, tp, d_un, t_un, last, flagged


def driver_points_index(season):
    """For the results scraper: (code->driverId, {(driverId,round,slot): points},
    {driverId: wildcard}) from the official driver standings. This is the correct
    source of F1A per-race points (the old computed POINTS tables were wrong)."""
    import json
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _rp = os.path.join(repo, "constructors", str(season), "drivers.json")
    roster = json.load(open(_rp)) if os.path.exists(_rp) else {}
    code2id = {i["Driver"]["code"]: i["Driver"]["driverId"] for i in roster.values()}
    drivers, _, _ = parse(BASE.format(kind="Driver", sid=SEASON_ID[season]),
                          lambda c: code2id.get(c), "driverId", "driver", slots_for(season))
    _assign_ids(drivers)                           # collision-safe ids (code for same-surname)
    pts, wild = {}, {}
    for e in drivers:
        did = e["driverId"]
        code2id[e["code"]] = did                   # include standings-only drivers (wildcards)
        wild[did] = e.get("wildcard", False)
        for br in e["byRound"]:
            for slot in ("race0", "race1", "race2", "race3"):
                if slot in br and str(br[slot]).lstrip("-").isdigit():
                    pts[(did, br["round"], slot)] = br[slot]
    return code2id, pts, wild


def main():
    ap = argparse.ArgumentParser(description="Update F1A standings from f1academy.com")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    if a.season not in SEASON_ID:
        sys.exit(f"Unknown season {a.season}. Known: {sorted(SEASON_ID)}")
    dp, tp, du, tu, lr, flagged = build(a.season)
    print(f"Wrote {dp} ({lr} rounds)")
    print(f"Wrote {tp}")
    if flagged:
        print(f"  wildcards tagged (confirm): {flagged}")
    if du:
        print(f"  ⚠ drivers not in roster: {du}")
    if tu:
        print(f"  ⚠ teams not in roster: {tu}")
    if a.push:
        import subprocess
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        subprocess.run(["git", "-C", repo, "add", "standings"], check=True)
        subprocess.run(["git", "-C", repo, "commit", "-m",
                        f"Add F1A {a.season} standings (driver + team)"], check=True)
        subprocess.run(["git", "-C", repo, "push"], check=True)


if __name__ == "__main__":
    main()
