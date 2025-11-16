
# CanIKona.app

<div align="center">
🏊‍♂️ + 🚴‍♂️ + 🏃‍♂️ = 🏝️ ?
</div>

CanIKona.app is a web application for tracking Ironman and Ironman 70.3 age-graded results and World Championship slot allocations. Until Ironman starts producing real-time age-graded results, this app is the easiest way to determine how far down you are in the rolldowns and how you compare to other competitors prior to Ironman's finalized awards ceremony list.

The project was created after I drove home from Ironman 70.3 Wisconsin in 2025 and realized I didn't know whether I needed to turn around and head back downtown to the awards ceremony to claim a rolldown World Championship slot. It also proved useful the next day during Ironman Wisconsin while I was cheering on friends and seeing how they stacked up. Hopefully others will find it as useful as I do.

## 🚀 Live Site

You can use the app right now at: [https://www.canikona.app/](https://www.canikona.app/)

## 🛠️ Local Deployment

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Tapin42/canikona.git
   cd canikona
   ```
2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
3. **Run the app locally:**
   ```bash
   python app.py
   ```
4. Open your browser and go to [http://localhost:5000](http://localhost:5000)

## 🤝 Contributing

- Anyone can file issues for bug reports or enhancement requests.
- I am very open to taking pull requests from anyone who wants to contribute—just fork the repo, make your changes, and submit a PR!

## 🧠 Caching of live RTRT.me results

To reduce load on RTRT.me and speed up page loads, live results are cached on disk under `data/`:

- 140.6 races:
   - `data/140.6/in_progress/<RACE_KEY>.json`
   - `data/140.6/final/<RACE_KEY>.json`
- 70.3 races:
   - `data/70.3/men/in_progress/<RACE_KEY>.json`
   - `data/70.3/men/final/<RACE_KEY>.json`
   - `data/70.3/women/in_progress/<RACE_KEY>.json`
   - `data/70.3/women/final/<RACE_KEY>.json`

Behavior:
- If a race has `official_ag` configured and a `final` cache exists, live requests are served from the `final` cache without hitting the API.
- If `official_ag` is configured but `final` is missing, the app will fetch once from RTRT.me, process results, store to `final`, and serve that.
- If no `official_ag` or no `final` is present, the app uses `in_progress` if it exists and is “fresh.” Otherwise, it fetches, processes, writes to `in_progress`, and serves.

Freshness window for `in_progress` is configurable via environment variable (default 60 seconds):

```bash
export CACHE_FRESHNESS_SECONDS=60
```

Implementation lives in `parse_live_data.get_processed_results_cached` and `cache_utils.py`.

## 🌀 Dynamic Slot Allocation Persistence

For races using the **split-dynamic** slot policy (post-announcement 140.6 gender split), the app computes slot distribution after starter counts stabilize (1 hour after earliest start). To avoid recomputing on every restart and to preserve historical allocation context, dynamic data is persisted to disk:

File: `data/dynamic_slots.json`

Schema:
```jsonc
{
   "RACEKEY": {
      "dynamic_slots": {
         "men": { "winner_slots": 10, "pool_slots": 25, "total_slots": 35 },
         "women": { "winner_slots": 9, "pool_slots": 21, "total_slots": 30 },
         "computed_at": 1731686400
      },
      "started_counts": { "men": 1780, "women": 1450, "computed_at": 1731686400 }
   }
}
```

Behavior:
* Starter counts are persisted as soon as they are successfully fetched.
* Dynamic slot allocation is persisted immediately after computation.
* On app startup, races are hydrated with any saved `started_counts` and `dynamic_slots` values so UI annotations are instantly available.
* Writes use an atomic temp-file + rename strategy to prevent partial JSON corruption.

Implementation: `parse_live_data.py` (`persist_dynamic_state`, `hydrate_race_dynamic`) and `cache_utils.write_json_atomic`.

If you need to reset dynamic data (e.g., for a recomputation test), delete `data/dynamic_slots.json` and restart the app. It will be recreated automatically.

## 📐 Versioned Age-Graded (AG) adjustments

AG factors are now versioned with effective dates so historic races always use the same set they were originally processed with.

- Files live under `adjustments/`:
   - `adjustments/manifest.json` – list of versions with:
      - `id`, `distance` ("70.3" or "140.6"), `effective_from` (YYYY-MM-DD), and `file` path
   - Per-version factor files (e.g., `adjustments/70.3/baseline.json`)
- Per-race locking is stored in `data/ag_assignments.json`.
   - New schema (supports keys that have both 70.3 and 140.6 at the same time):
      - `{ "<race_key>": { "per_distance": { "70.3": "<version-id>", "140.6": "<version-id>" } } }`
   - Legacy entries are still honored and auto-migrated when encountered:
      - `{ "<race_key>": { "adjustments_version": "<version-id>" } }`
- At runtime, the app will:
   1) Look up a race in `ag_assignments.json`.
   2) If missing, select the latest version with `effective_from <= race.date`.
   3) Persist the selection so it doesn’t change later.

CLI helper:

```bash
scripts/manage_ag_versions.py list-versions
scripts/manage_ag_versions.py dry-run
scripts/manage_ag_versions.py write-assignments  # pre-lock all races (per-distance)
```

Notes:
- If you add a new set from Ironman, add a new entry in `manifest.json` with its `effective_from` and point `file` to the new factors JSON.

## 🧩 Git hooks: JSON Unicode validation

This repo includes Git hooks that prevent committing or pushing JSON files that contain invalid Unicode or malformed JSON.

What it does:
- Validates staged `.json` files on commit
- Validates all tracked `.json` files on push (lightweight sanity check)
- Checks that files are valid UTF-8, parse as JSON, and contain no unpaired surrogate code points

Enable the hooks for your clone (one-time):

```bash
git config core.hooksPath .githooks
```

Manual run (optional):

```bash
python3 scripts/validate_json_unicode.py $(git ls-files '*.json')
```

Bypass temporarily (not recommended):

```bash
git commit --no-verify -m "your message"
```

## 📫 Contact

For questions or further conversation, feel free to reach out:
- **Email:** navratil@gmail.com

## ⚠️ Disclaimer

I do not claim any copyright or affiliation with Ironman, Ironman 70.3, or RTRT.me. This project is a fan-made tool for the triathlon community.

---

Thank you for checking out CanIKona.app!
