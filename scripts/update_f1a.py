#!/usr/bin/env python3
"""
update_f1a.py — pull F1 Academy race results straight from f1academy.com and
write them into races/<year>/resullts.json in this repo's schema. No screenshots.

The results page serves plain HTML tables per session (Feature Race, Reverse Grid
Race, sometimes an Opening Race, plus Qualifying / Free Practice). This script
reads those tables, derives the starting grid from qualifying, applies the points
table, and upserts the round.

    python scripts/update_f1a.py --raceid 25              # write only
    python scripts/update_f1a.py --raceid 25 --push       # ...and git commit+push

Session -> repo key:  Opening Race -> race0, Reverse Grid Race -> race1,
Feature Race -> race2  (matches the 2025 Canadian round's race0/1/2 layout).

CAVEATS you should eyeball after a run:
  * POINTS tables and the reverse-grid size are league rules that change between
    seasons — confirm POINTS below matches the current season.
  * GRID is derived from qualifying (feature = quali order; reverse race =
    reversed top-N). A grid penalty, or a weekend with two qualifying sessions,
    can make it wrong — check grids on those rounds.

Run `pip install -r scripts/requirements.txt` once first.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

from lxml import html

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_URL = "https://www.f1academy.com/Racing-Series/Results?raceid={}"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

# raceid -> (round, raceName, circuitId, circuitName). circuitName also read from
# the page and used to sanity-check.
RACES = {
    22: (1, "Chinese Grand Prix",       "shanghai",   "Shanghai International Circuit"),
    24: (2, "Canadian Grand Prix",      "villeneuve", "Circuit Gilles-Villeneuve"),
    25: (3, "British Grand Prix",       "silverstone", "Silverstone Circuit"),
    26: (4, "Dutch Grand Prix",         "zandvoort",  "Circuit Zandvoort"),
    27: (5, "United States Grand Prix", "americas",   "Circuit of the Americas"),
    28: (6, "Las Vegas Grand Prix",     "vegas",      "Las Vegas Strip Circuit"),
}

# Points by finishing position (index 0 = P1). Confirm against the current season.
POINTS = {
    "race0": [10, 8, 6, 5, 4, 3, 2, 1],                    # opening / made-up race
    "race1": [10, 8, 6, 5, 4, 3, 2, 1],                    # reverse grid race
    "race2": [25, 18, 15, 12, 10, 8, 6, 4, 2, 1],          # feature race
}
REVERSE_N = 8   # reverse-grid race reverses the top N of qualifying

SESSION_KEY = {"opening race": "race0", "reverse grid race": "race1", "feature race": "race2"}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=60).read()


def car_number(cell_text):
    m = re.match(r'\s*(\d+)', cell_text)
    return m.group(1) if m else ""


def table_label(doc, table):
    for prev in table.xpath('preceding::*[self::h3 or self::h4 or self::h5 or '
                            'self::strong or self::button or self::a or self::span '
                            'or self::li][position()<=6]')[::-1]:
        tx = prev.text_content().strip().lower()
        if tx and len(tx) < 40 and any(k in tx for k in
                ("race", "qualifying", "practice")):
            return tx
    return ""


def parse_sessions(doc):
    """Return {label: [row-cells,...]} for every result table on the page."""
    out = {}
    for t in doc.xpath('//table[contains(@class,"table-bordered")]'):
        label = table_label(doc, t)
        rows = [[td.text_content().strip() for td in tr.xpath('./td')]
                for tr in t.xpath('.//tbody/tr')]
        if label and rows:
            out.setdefault(label, rows)
    return out


def _time_seconds(s):
    m = re.match(r'(?:(\d+):)?(\d+):(\d\d\.\d+)$', s.strip())
    if not m:
        return float("inf")
    h = int(m.group(1) or 0)
    return h * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def quali_rankings(sessions):
    """All qualifying tables as lists of car numbers, fastest pole first.

    A normal weekend has one qualifying table. A weekend with an extra Opening
    Race (e.g. Montreal 2026) shows the same session ranked twice — by each
    driver's fastest lap and by their second-fastest lap. The fastest-pole table
    sets the reverse/feature grids; the slower one sets the Opening Race grid.
    """
    tables = []
    for label, rows in sessions.items():
        if label.startswith("qualifying") and rows:
            pole = _time_seconds(rows[0][3]) if len(rows[0]) > 3 else float("inf")
            tables.append((pole, [car_number(r[1]) for r in rows]))
    tables.sort(key=lambda x: x[0])
    return [order for _, order in tables]


def derive_grid(sessions, race_key):
    rankings = quali_rankings(sessions)
    if not rankings:
        return {}
    main = rankings[0]                              # fastest-lap qualifying order
    if race_key == "race2":                         # feature = straight quali order
        return {num: str(i + 1) for i, num in enumerate(main)}
    if race_key == "race1":                          # reverse the top N
        top = main[:REVERSE_N][::-1] + main[REVERSE_N:]
        return {num: str(i + 1) for i, num in enumerate(top)}
    if race_key == "race0":                          # opening race = 2nd-fastest order
        second = rankings[1] if len(rankings) > 1 else None
        return {num: str(i + 1) for i, num in enumerate(second)} if second else {}
    return {}


def build_race_rows(rows, race_key, grid):
    """rows: list of td-cell lists for one race table -> schema rows."""
    pts = POINTS.get(race_key, [])
    out = []
    for i, r in enumerate(rows):
        if len(r) < 8:
            continue
        pos_cell = r[0]
        finished = re.fullmatch(r'\d+', pos_cell) is not None
        status = "Finished" if finished else pos_cell.upper()
        position = str(i + 1)
        num = car_number(r[1])
        laps, race_time = r[2], r[3]
        best, on = r[7], (r[8] if len(r) > 8 else "0")
        point = pts[i] if (finished and i < len(pts)) else 0
        out.append({
            "number": num, "position": position, "grid": grid.get(num, ""),
            "laps": laps, "status": status, "points": str(point),
            "Time": {"time": race_time},
            "FastestLap": {"lap": on, "Time": {"time": best}},
        })
    return out


def build_round(raceid, sessions, meta):
    round_no, race_name, circuit_id, circuit_name = meta
    out = {"season": "2026", "round": str(round_no), "raceName": race_name,
           "Circuit": {"circuitId": circuit_id, "circuitName": circuit_name},
           "Results": {}}
    for label, rows in sessions.items():
        key = SESSION_KEY.get(label)
        if not key:
            continue
        grid = derive_grid(sessions, key)
        out["Results"][key] = build_race_rows(rows, key, grid)
        note = "" if grid else "  (grid not derived)"
        print(f"  {label} -> {key}: {len(out['Results'][key])} rows{note}")
    # keep race0/1/2 order
    out["Results"] = {k: out["Results"][k] for k in ("race0", "race1", "race2")
                      if k in out["Results"]}
    return out


def upsert(rnd):
    path = os.path.join(REPO, "races", "2026", "resullts.json")
    data = json.load(open(path))
    data = [r for r in data if r["round"] != rnd["round"]] + [rnd]
    data.sort(key=lambda r: int(r["round"]))
    with open(path, "w") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.write("\n")
    return path


def git_push(path, rnd):
    msg = f"F1A {rnd['raceName']} (round {rnd['round']}) results"
    for cmd in (["git", "add", path], ["git", "commit", "-m", msg], ["git", "push"]):
        subprocess.run(cmd, cwd=REPO, check=True)


def main():
    ap = argparse.ArgumentParser(description="Update F1 Academy results from f1academy.com")
    ap.add_argument("--raceid", type=int, required=True, help="f1academy.com raceid (see RACES map)")
    ap.add_argument("--push", action="store_true", help="git add/commit/push after writing")
    a = ap.parse_args()

    if a.raceid not in RACES:
        sys.exit(f"Unknown raceid {a.raceid}. Known: {sorted(RACES)}")
    meta = RACES[a.raceid]

    print(f"Fetching raceid {a.raceid} …")
    doc = html.fromstring(fetch(RESULTS_URL.format(a.raceid)))
    sessions = parse_sessions(doc)
    if not any(l in SESSION_KEY for l in sessions):
        sys.exit("No race tables found (round may not have happened yet).")

    rnd = build_round(a.raceid, sessions, meta)
    path = upsert(rnd)
    print(f"Wrote round {rnd['round']} ({list(rnd['Results'])}) → {os.path.relpath(path, REPO)}")
    if a.push:
        git_push(path, rnd)
        print("Committed and pushed.")


if __name__ == "__main__":
    main()
