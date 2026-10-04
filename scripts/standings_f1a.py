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
# OR=opening->race0, RG=reverse->race1, FR=feature->race2
SLOTS = {2: ["race1", "race2"], 3: ["race0", "race1", "race2"]}
# Wildcards are detected from the "(WCD)" label f1academy puts in the driver name
# (authoritative); no hardcoded list needed.


def _scores(td):
    # the per-session cells have class exactly "score" (inside a "score-wrapper")
    return [C.cell_text(s) for s in td.xpath(
        './/*[contains(concat(" ", normalize-space(@class), " "), " score ")]')]


def parse(url, resolve, id_field, name_field):
    table = C.fetch_html(url).xpath("//table")[0]
    out, unmatched = [], []
    for tr in table.xpath(".//tbody/tr"):
        cells = tr.xpath("./th|./td")
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
            slots = SLOTS.get(len(sc))
            if not slots:
                continue
            rec = {"round": str(rn)}
            for slot, v in zip(slots, sc):
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
    return out, unmatched


def build(season):
    import json
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roster = json.load(open(os.path.join(repo, "constructors", str(season), "drivers.json")))
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

    drivers, d_un = parse(BASE.format(kind="Driver", sid=sid), resolve_driver, "driverId", "driver")
    tms, t_un = parse(BASE.format(kind="Team", sid=sid), resolve_team, "constructorId", "team")

    for e in drivers:
        if e["driverId"] is None:
            e["driverId"] = C.slug(C.surname_of(e["driver"]))
    for e in tms:
        if e["constructorId"] is None:
            e["constructorId"] = C.slug(e["team"])

    last = max((int(r["round"]) for e in drivers for r in e["byRound"]), default=0)
    meta = {"season": str(season), "series": "f1a", "updated": C.today_iso(), "lastRound": last}
    dp = C.write_json(os.path.join(repo, "standings", str(season), "drivers.json"),
                      {**meta, "source": BASE.format(kind="Driver", sid=sid), "Standings": drivers})
    tp = C.write_json(os.path.join(repo, "standings", str(season), "teams.json"),
                      {**meta, "source": BASE.format(kind="Team", sid=sid), "Standings": tms})
    flagged = [f"{e['driver']} ({e['code']})" for e in drivers if e["wildcard"]]
    return dp, tp, d_un, t_un, last, flagged


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
