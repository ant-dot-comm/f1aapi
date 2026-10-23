"""
series_config.py — everything series-specific for build_mm.py / stats.py (f1a-api).

The scripts themselves are identical in f2api and f1a-api; this file is the one
that differs. Edit it when a new season starts (YEARS, LIVE_SEASON,
SCHEDULED_ROUNDS, FUTURE_ROUNDS), when motorsportmag lists a team it has never
seen (TEAMS), or when a finished season needs a points Bonus that no race row
carries (BONUSES).
"""
SERIES = "f1a"
ROSTER_DIR = "constructors"          # <ROSTER_DIR>/<year>/{registry,drivers,<team>}.json
YEARS = list(range(2023, 2027))
LIVE_SEASON = 2026                   # refetched on every build; may be incomplete
SCHEDULED_ROUNDS = {2026: 6}         # finished seasons use their own round count
LEGACY_RACES_JSON = True             # races/races.json (year -> raceName -> meeting_key)
LEGACY_TEAM_ROSTERS = True           # constructors/<year>/<team>.json driver lists


def wildcards_score_for_team(year):
    """Wildcard entries score for their team from 2025 (matches the official team tables)."""
    return year >= 2025


def era_of(year):
    return "f1a"


def champ_name(year):
    return "F1 Academy"


def champ_slug(year):
    """motorsportmag championship page slug."""
    return f"{year}-f1-academy"


def scales(year):
    """Points paid by finishing position, per race type (before pole / fastest-lap bonuses).
    2023 ran two full-points races and a reverse-grid race per weekend, 2024 two
    full-points races, 2025 on a reverse-grid race plus the feature race."""
    return {"feature": [25, 18, 15, 12, 10, 8, 6, 4, 2, 1], "sprint": [10, 8, 6, 5, 4, 3, 2, 1]}


def legacy_keys(year, sessions):
    """Results keys for a weekend's races (running order), as the original API used them:
    to 2024 race1/race2/race3 in running order; from 2025 the reverse-grid race = race1,
    the feature race = race2 and an extra (opening / rescheduled) race = race0."""
    if year <= 2024:
        return [f"race{i}" for i in range(1, len(sessions) + 1)]
    keys = [None] * len(sessions)
    sprints = [i for i, s in enumerate(sessions) if s["type"] == "sprint"]
    feats = [i for i, s in enumerate(sessions) if s["type"] == "feature"]
    if sprints:
        keys[sprints[0]] = "race1"
    if feats:
        keys[feats[-1]] = "race2"
    extra = iter(["race0", "race3"])
    return [k or next(extra) for k in keys]


def session_label(year, rtype, index, count, order, sessions):
    if year <= 2024:
        return f"Race {order}"
    if rtype == "sprint":
        return "Reverse Grid Race"
    return "Opening Race" if count > 1 and index < count else "Feature Race"


def race_name(year, circuit_id, default, visit):
    if year == 2023 and circuit_id == "valencia":
        return "Valencia Grand Prix"
    if year == 2023 and circuit_id == "ricard":
        return "Le Castellet Grand Prix"
    return default


# Rounds of the live season that have not run yet (motorsportmag has no page for them).
FUTURE_ROUNDS = {2026: [{"round": 5, "mm": "austin-cota", "circuitName": "Circuit of the Americas"},
                        {"round": 6, "mm": "las-vegas-boulevard", "circuitName": "Las Vegas Strip Circuit"}]}

# One constructorId per entrant. A rename keeps the id; a takeover starts a new id linked
# by predecessor/successor. "mm" lists motorsportmag team slugs (optionally "@YYYY-YYYY").
TEAMS = [
    {"id": "art", "name": "ART Grand Prix", "shortName": "Art", "mm": ["art-grand-prix"]},
    {"id": "campos", "name": "Campos Racing", "shortName": "Campos", "mm": ["adrian-campos-motorsport"]},
    {"id": "carlin", "name": "Rodin Carlin", "shortName": "Carlin", "mm": ["carlin"], "successor": "rodin"},
    {"id": "rodin", "name": "Rodin Motorsport", "shortName": "Rodin", "mm": ["rodin-motorsport"], "predecessor": "carlin"},
    {"id": "hitech", "name": "Hitech", "shortName": "Hitech", "mm": ["hitech-racing"]},
    {"id": "mp", "name": "MP Motorsport", "shortName": "MP", "mm": ["mp-motorsport"]},
    {"id": "prema", "name": "Prema Racing", "shortName": "Prema", "mm": ["prema-powerteam"]},
]

# Championship points no race row carries: pole position for a race that was then not
# run. Every finished season must add up to motorsportmag's table, so these are the
# documented exceptions (driver = motorsportmag driver slug).
BONUSES = [
    {"season": 2024, "round": 6, "driver": "doriane-pin", "type": "pole", "points": 2,
     "note": "Pole position for Race 1, which was not run"},
    {"season": 2025, "round": 3, "driver": "chloe-chambers", "type": "pole", "points": 2,
     "note": "Pole position; the feature race was cancelled"},
]

# Rounds motorsportmag has no race for, kept so round numbers match the official calendar.
EMPTY_ROUNDS = []

# Sessions that were scheduled but not run (order = their place in the weekend).
CANCELLED = [
    {"season": 2024, "round": 6, "type": "feature", "order": 1, "note": "Cancelled"},
    {"season": 2025, "round": 3, "type": "feature", "order": 2, "note": "Cancelled (weather)"},
]

# Known, explained differences between race rows and the official table (season, driverId).
ACCEPT_DIFFS = []

# Official post-race classification changes motorsportmag has not applied yet (taken from
# f1academy.com's classification). The build prints a NOTE once motorsportmag matches, so
# stale entries can be dropped. A finished season with corrections takes its standings from
# the corrected race rows, since motorsportmag's own table carries the same error.
CORRECTIONS = [{'season': 2024,
  'round': 7,
  'type': 'feature',
  'index': 3,
  'note': 'Official F1 Academy classification (post-race penalty for Edgar)',
  'set': {'maya-weug': {'position': 1, 'points': 26},
          'abbi-pulling': {'position': 2, 'points': 20},
          'nerea-marti': {'position': 3, 'points': 15},
          'tina-hausmann': {'position': 4, 'points': 12},
          'doriane-pin': {'position': 5, 'points': 10},
          'carrie-schreiner': {'position': 6, 'points': 8},
          'lola-lovinfosse': {'position': 7, 'points': 6},
          'aurelia-nobels': {'position': 8, 'points': 4},
          'emely-de-heus': {'position': 9, 'points': 2},
          'hamda-al-qubaisi': {'position': 10, 'points': 1},
          'logan-hannah': {'position': 11, 'points': 0},
          'jessica-edgar': {'position': 12, 'points': 0},
          'amna-al-qubaisi': {'position': 13, 'points': 0},
          'bianca-bustamante': {'position': 14, 'points': 0},
          'lia-block': {'position': 15, 'points': 0},
          'chloe-chambers': {'position': 16, 'points': 0}}},
 {'season': 2026,
  'round': 2,
  'type': 'feature',
  'index': 1,
  'note': 'Official F1 Academy classification (bonus points)',
  'set': {'alisha-palmowski': {'position': 1, 'points': 26},
          'megan-bruce': {'position': 2, 'points': 18},
          'mathilda-paatz': {'position': 3, 'points': 15},
          'rafaela-ferreira': {'position': 4, 'points': 12},
          'alba-larsen': {'position': 5, 'points': 10},
          'ella-lloyd': {'position': 6, 'points': 8},
          'kaylee-countryman': {'position': 7, 'points': 6},
          'ella-stevens': {'position': 8, 'points': 4},
          'nina-gademan': {'position': 9, 'points': 2},
          'emma-felbermayr': {'position': 10, 'points': 1},
          'autumn-fisher': {'position': 11, 'points': 0},
          'jade-jacquet': {'position': 12, 'points': 0},
          'rachel-robertson': {'position': 13, 'points': 0},
          'natalia-granada': {'position': 14, 'points': 0},
          'lisa-billard': {'position': 15, 'points': 0},
          'payton-westcott': {'position': 16, 'points': 0},
          'ava-dobson': {'position': 17, 'points': 0},
          'esmee-kosterman': {'position': 18, 'points': 0}}},
 {'season': 2026,
  'round': 2,
  'type': 'sprint',
  'index': 1,
  'note': 'Official F1 Academy classification (post-race penalties for Larsen and Lloyd)',
  'set': {'mathilda-paatz': {'position': 1, 'points': 10},
          'kaylee-countryman': {'position': 2, 'points': 8},
          'payton-westcott': {'position': 3, 'points': 6},
          'natalia-granada': {'position': 4, 'points': 5},
          'lisa-billard': {'position': 5, 'points': 5},
          'emma-felbermayr': {'position': 6, 'points': 3},
          'megan-bruce': {'position': 7, 'points': 2},
          'rafaela-ferreira': {'position': 8, 'points': 1},
          'esmee-kosterman': {'position': 9, 'points': 0},
          'alisha-palmowski': {'position': 10, 'points': 0},
          'alba-larsen': {'position': 11, 'points': 0},
          'jade-jacquet': {'position': 12, 'points': 0},
          'ella-lloyd': {'position': 13, 'points': 0},
          'autumn-fisher': {'position': 14, 'points': 0},
          'ella-stevens': {'position': 15, 'points': 0},
          'nina-gademan': {'position': 16, 'points': 0},
          'rachel-robertson': {'position': 17, 'points': 0},
          'ava-dobson': {'position': 18, 'points': 0}}}]
