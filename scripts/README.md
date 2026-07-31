# F1 Academy results updater

Scrapes the result tables from f1academy.com and writes them into
`races/2026/resullts.json` — no screenshots.

## Setup (once)

```bash
pip install -r scripts/requirements.txt
```

## Each race weekend

```bash
python scripts/update_f1a.py --raceid 25          # write locally
python scripts/update_f1a.py --raceid 25 --push    # ...and git commit + push
```

`--raceid` is the `raceid` in the f1academy.com results URL
(`/Racing-Series/Results?raceid=NN`); see the `RACES` map in `update_f1a.py`.

## What it derives

- **Session → key:** Opening Race → `race0`, Reverse Grid Race → `race1`,
  Feature Race → `race2`.
- **Grid** (the page doesn't list it) from qualifying: feature = quali order,
  reverse race = reversed top-8. On a weekend with an Opening Race, qualifying is
  shown twice (fastest and second-fastest lap); the faster ranking sets
  reverse/feature, the slower one sets the Opening Race grid.
- **Points** from the tables in `POINTS` (race1 10-8-6-5-4-3-2-1,
  race2 25-18-15-12-10-8-6-4-2-1).

## Notes / gotchas

- `POINTS` and the reverse-grid size are league rules — confirm they match the
  season before trusting a backfill.
- Grid derivation assumes no grid penalties reshuffled the order; check grids on
  rounds where a penalty was applied.
