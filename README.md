
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

## 📐 Versioned Age-Graded (AG) adjustments

AG factors are now versioned with effective dates so historic races always use the same set they were originally processed with.

- Files live under `adjustments/`:
   - `adjustments/manifest.json` – list of versions with:
      - `id`, `distance` ("70.3" or "140.6"), `effective_from` (YYYY-MM-DD), and `file` path
   - Per-version factor files (e.g., `adjustments/70.3/baseline.json`)
- Per-race locking is stored in `data/ag_assignments.json` as:
   - `{ "<race_key>": { "adjustments_version": "<version-id>" } }`
- At runtime, the app will:
   1) Look up a race in `ag_assignments.json`.
   2) If missing, select the latest version with `effective_from <= race.date`.
   3) Persist the selection so it doesn’t change later.

CLI helper:

```bash
scripts/manage_ag_versions.py list-versions
scripts/manage_ag_versions.py dry-run
scripts/manage_ag_versions.py write-assignments  # pre-lock all races
```

Notes:
- If you add a new set from Ironman, add a new entry in `manifest.json` with its `effective_from` and point `file` to the new factors JSON.


## 🤝 Contributing

- Anyone can file issues for bug reports or enhancement requests.
- I am very open to taking pull requests from anyone who wants to contribute—just fork the repo, make your changes, and submit a PR!

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
