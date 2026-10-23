# Building the F1 Academy API

Everything is rebuilt from motorsportmagazine.com, the source of truth for every season.
Only the Python standard library is needed.

## After each race weekend

```bash
python scripts/build_mm.py      # refetches the live season, rebuilds every season
python scripts/stats.py         # rebuilds stats/
git add -A && git commit -m "f1a: <event>" && git push
```

`build_mm.py --push` / `stats.py --push` commit and push for you. Responses are cached in `$MM_CACHE`
(default: the system temp folder), so a rebuild only refetches the live season; `--cached` skips even that.

The build stops without writing anything when a finished season's race points + `BONUSES` don't add
up to motorsportmag's championship table, when motorsportmag lists a team or circuit it hasn't seen,
or when two different drivers would get the same `driverId`.

## Where the rules live

- `series_config.py` (differs per repo): seasons, live season and its scheduled rounds, points scales,
  session labels, results-key layout, race names, the team lineage table (`TEAMS`: one `constructorId`
  per entrant, renames keep it, takeovers link `predecessor` / `successor`), `BONUSES`, cancelled sessions,
  and `CORRECTIONS` for official post-race changes motorsportmag hasn't applied yet (the build prints a
  NOTE once motorsportmag catches up so the entry can be removed).
- `people_overrides.json` (per repo), keyed by motorsportmag driver slug: accented / compound names,
  official 3-letter codes (these match the portrait files on the site), wildcard seasons.
- `motorsportmag.py`, `build_mm.py`, `stats.py`, `common.py` are identical in f2api and f1a-api;
  copy them across when you change them. New venues go in `motorsportmag.CIRC`.

## New season

Add the year to `YEARS`, set `LIVE_SEASON`, `SCHEDULED_ROUNDS` and `FUTURE_ROUNDS`, and add codes
for new drivers to `people_overrides.json` (otherwise a code is generated from the surname).

## Legacy

`scripts/legacy/` holds the retired FIA / f1academy.com scrapers. They write the old schema and would
overwrite the motorsportmag data; they are kept for reference only.
