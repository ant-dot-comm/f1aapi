# F1 Academy API

Static JSON served from GitHub Pages at `https://ant-dot-comm.github.io/f1aapi`. Seasons: 2023–2026.

Every file is built by `scripts/build_mm.py` from [motorsportmagazine.com](https://www.motorsportmagazine.com/database/)
(see `scripts/README.md`). Points are never recalculated by the consumer: race rows carry the points
each driver was awarded, and a finished season's rows (plus `Bonuses`) add up exactly to the official
championship table — the build refuses to publish otherwise.

## Files

| Path | What |
|---|---|
| `seasons.json` | Every published season: `season`, `series`, `championship`, `rounds`, `scheduledRounds`, `complete`, `champion`, `teamChampion`. Build year menus from this. |
| `drivers/index.json` | One row per person: `driverId`, `name`, `givenName`, `familyName`, `code`, `codeSource` (`official` / `generated`), `nationality`, `countryCode`, `dateOfBirth`, `birthplace`, `seasons`. |
| `teams/index.json` | One row per entrant: `constructorId`, `name`, `shortName`, `names` (by season), `seasons`, `predecessor`, `successor`. |
| `races/<year>/results.json` | Weekends (see below). `resullts.json` is the same file under its original name, kept for older clients. |
| `races/<year>/calendar.json` | `Races`: `round`, `raceId`, `raceName`, `circuitId`, `circuitName`, `locality`, `country`, `date`, `status` (`complete` / `scheduled`). |
| `standings/<year>/drivers.json` | Championship table: `position`, `driverId`, `code`, `driver`, `givenName`, `familyName`, `nationality`, `constructorId` (team at the last round), `teams` (if they changed team), `points`, `wins`, `byRound` (`round`, `points`, `total`, `position`), `wildcard`. |
| `standings/<year>/teams.json` | `position`, `constructorId`, `team` (that season's entrant name), `points`, `wins`, `byRound`. |
| `constructors/<year>/registry.json` | Who drove which car number for which team, by round. |
| `constructors/<year>/drivers.json` | Legacy roster keyed by car number (`Driver`, `Driver2`, …, `Constructor`). |
| `stats/index.json`, `stats/drivers/<driverId>.json`, `stats/teams/<constructorId>.json` | Career and per-season stats, every result, and per team its `drivers`, `leaders` (top 3 by starts / wins / podiums / poles / points / titles) and `championDrivers`. |
| `races/racesbyMK.json` | Legacy calendar keyed by `raceId` (`<year><round>`). |

## A weekend in `results.json`

```json
{
  "season": "2026", "round": "12", "raceName": "Azerbaijan Grand Prix", "date": "2026-09-26",
  "Circuit": {"circuitId": "baku", "circuitName": "Baku", "Location": {"locality": "Baku", "country": "Azerbaijan"}},
  "Sessions": [
    {"key": "race1", "type": "sprint", "label": "Sprint Race", "order": 1, "date": "2026-09-25", "laps": 21,
     "fastestLap": {"driverId": "camara", "time": "1:56.855"}},
    {"key": "race2", "type": "feature", "label": "Feature Race", "order": 2, "pole": "stenshorne"}
  ],
  "Results": {"race1": [{"number": "1", "driverId": "camara", "constructorId": "invicta", "grid": "3",
                         "position": "1", "status": "Finished", "points": "10", "...": "..."}]},
  "Bonuses": [{"driverId": "...", "constructorId": "...", "type": "pole", "points": "2", "note": "..."}]
}
```

- **Read race type and label from `Sessions`, never from the key.** `type` is `sprint` or `feature`;
  labels are 2023–2024 `Race 1/2/3`; from 2025 `Reverse Grid Race`, `Feature Race`, `Opening Race` (the extra feature on a three-race weekend). `order` is the running order.
- Keys follow the original layout so older clients keep working: `race1` = sprint, `race2` = feature,
  `race0` = a weekend's extra race.
- Flags on a session: `halfPoints`, `noPoints` (race stopped before points were due), `cancelled`
  (scheduled but not run; `key` is null and there are no results).
- `Bonuses` are championship points no race row carries (pole position for a race that was then not run).
- `position` is `"NC"` for unclassified rows; `status` is `Finished`, `DNF`, `DNS`, `DSQ` or `NC`.
