"""
Update RTRT keys and earliestStartTimes in races.json by inferring event keys
and validating them against the RTRT API.

Usage:
  RTRT_APPID=... RTRT_TOKEN=... python scripts/update_rtrt_keys.py [--dry-run]

Notes:
  - Reads and writes races.json in-place (creates a timestamped backup in backup/)
  - Only updates entries missing `key` and/or `earliestStartTime`
  - Key inference uses normalized race names and multiple candidate patterns
  - If only earliestStartTime is missing but key exists, it will try to fetch it

Environment variables:
  - RTRT_APPID: required to call RTRT API
  - RTRT_TOKEN: required to call RTRT API
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import unicodedata

import requests


ROOT = Path(__file__).resolve().parents[1]
RACES_PATH = ROOT / "races.json"
BACKUP_DIR = ROOT / "backup"


def load_env() -> Tuple[str, str]:
    appid = os.environ.get("RTRT_APPID")
    token = os.environ.get("RTRT_TOKEN")
    if not appid or not token:
        raise SystemExit(
            "Missing RTRT_APPID or RTRT_TOKEN environment variables. "
            "Set them and rerun."
        )
    return appid, token


def load_races(path: Path) -> List[Dict]:
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError("races.json should be a list of race objects")
    return data


def save_races(path: Path, races: List[Dict]) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    backup_path = BACKUP_DIR / f"races.json.{ts}"
    backup_path.write_text(json.dumps(load_races(path), ensure_ascii=False, indent=2))
    path.write_text(json.dumps(races, ensure_ascii=False, indent=2))


def strip_accents(s: str) -> str:
    # Normalize to ASCII, drop diacritics
    return (
        unicodedata.normalize("NFKD", s)
        .encode("ascii", "ignore")
        .decode("ascii")
    )


CANON_MAP = {
    # Common normalizations / irregulars
    "ST": "SAINT",
    "ST.": "SAINT",
    "ST-": "SAINT-",
    "ST-.": "SAINT-",
    "SÃO": "SAO",
    "SAO": "SAO",
    "HAVAII": "HAWAII",
    "HAWAI": "HAWAII",
    "HAWAI'I": "HAWAII",
    "HAWAIʻI": "HAWAII",
    "HAWAI\u02BBI": "HAWAII",
    "VITORIA": "VITORIA",
    "VITORIA-GASTEIZ": "VITORIAGASTEIZ",
    "AIX-EN-PROVENCE": "AIXENPROVENCE",
    "Porec": "POREC",
    "POREC": "POREC",
    "COEUR D'ALENE": "COEURDALENE",
    "COEUR D ALENE": "COEURDALENE",
    "LAKE PLACID": "LAKEPLACID",
    "WESTERN SYDNEY": "WESTERNSYDNEY",
    "LES SABLES D'OLONNE": "LESSABLESDOLONNE",
    "LES SABLES D OLONNE": "LESSABLESDOLONNE",
}


def canon_tokens(name: str) -> List[str]:
    # Uppercase, strip accents, unify punctuation
    base = strip_accents(name.upper())
    # Remove common prefixes/suffixes and qualifiers
    base = re.sub(r"\bIRONMAN\b", " ", base)
    base = re.sub(r"\bWORLD CHAMPIONSHIP\b", " ", base)
    base = re.sub(r"\bEUROPEAN CHAMPIONSHIP\b|\bEC\b", " ", base)
    base = re.sub(r"\bNORTH AMERICAN CHAMPIONSHIP\b|\bNAC\b", " ", base)
    base = re.sub(r"\bASIA\s*PACIFIC\b|\bAPAC\b", " ", base)
    base = re.sub(r"\bMIDDLE EAST CHAMPIONSHIP\b|\bMEC\b", " ", base)
    base = re.sub(r"\bAFRICA CHAMPIONSHIP\b|\bAC\b", " ", base)
    base = base.replace("703", " ")  # keep numeric marker separate
    base = re.sub(r"[\u2019'`’]", " ", base)  # apostrophes
    base = re.sub(r"[\./]", " ", base)
    base = re.sub(r"[()&]", " ", base)
    base = base.replace("-", " ")
    base = re.sub(r"\s+", " ", base).strip()

    tokens = base.split(" ")
    # Apply canonical replacements for multi-word phrases first
    joined = " ".join(tokens)
    for k, v in CANON_MAP.items():
        if k in joined:
            joined = joined.replace(k, v)
    tokens = joined.split(" ")
    # Remove empty tokens and generic words
    drop = {"IRONMAN", "IRONMAN70", "IRONMAN703", "TRIATHLON", "CHAMPIONSHIP", "WORLD"}
    tokens = [t for t in tokens if t and t not in drop]
    return tokens


def build_name_variants(name: str) -> List[str]:
    tokens = canon_tokens(name)
    if not tokens:
        return []
    # Primary forms
    concat = "".join(tokens)
    hyphen = "-".join(tokens)
    last = tokens[-1]
    variants = [concat, hyphen, last]
    # Also try last two tokens combined if available
    if len(tokens) >= 2:
        variants.append("".join(tokens[-2:]))
        variants.append("-".join(tokens[-2:]))
    # Deduplicate while preserving order
    seen = set()
    out = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def make_candidate_keys(name: str, year: int, distance: str) -> List[str]:
    suffix = "703" if str(distance) == "70.3" else ""
    candidates = []
    for variant in build_name_variants(name):
        if suffix:
            candidates.append(f"IRM-{variant}{suffix}-{year}")
        candidates.append(f"IRM-{variant}-{year}")
    # A couple of special-case fallbacks
    # Some events use city only or full composite name; variants should already cover this
    # Return unique list
    seen = set()
    uniq = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def rtrt_get_event(appid: str, token: str, key: str) -> Optional[Dict]:
    url = f"https://api.rtrt.me/events/{key}"
    try:
        r = requests.get(url, params={"appid": appid, "token": token}, timeout=10)
        if r.status_code == 200:
            return r.json()
        return None
    except requests.RequestException:
        return None


def dates_match(race_date: str, event: Dict) -> bool:
    # RTRT event JSON has fields like 'date' or nested; accept prefix match
    evt_date = event.get("date") or event.get("startDate") or ""
    return isinstance(evt_date, str) and evt_date.startswith(race_date)


def update_rtrt_info(races: List[Dict], appid: str, token: str, dry_run: bool = False) -> Tuple[int, int]:
    updated = 0
    checked = 0
    for race in races:
        date = race.get("date")
        name = race.get("name", "")
        distance = race.get("distance", "")
        year = int(date.split("-")[0]) if date else None
        if not date or not name or not distance or not year:
            continue

        has_key = bool(race.get("key"))
        has_time = bool(race.get("earliestStartTime"))
        if has_key and has_time:
            continue

        checked += 1

        # If key exists but time missing, try to fetch once
        if has_key and not has_time:
            evt = rtrt_get_event(appid, token, race["key"])
            if evt and dates_match(date, evt):
                est = evt.get("earliestStartTime") or evt.get("startTime")
                if est:
                    race["earliestStartTime"] = str(est)
                    updated += 1
            continue

        # Otherwise, infer key candidates
        for cand in make_candidate_keys(name, year, distance):
            evt = rtrt_get_event(appid, token, cand)
            if not evt:
                continue
            if not dates_match(date, evt):
                continue
            # Match found
            race["key"] = cand
            est = evt.get("earliestStartTime") or evt.get("startTime")
            if est:
                race["earliestStartTime"] = str(est)
            updated += 1
            break

    return updated, checked


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Infer and update RTRT keys/times in races.json")
    parser.add_argument("--dry-run", action="store_true", help="Do not write changes, just report")
    args = parser.parse_args(argv)

    try:
        appid, token = load_env()
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2

    races = load_races(RACES_PATH)
    missing = [r for r in races if not r.get("key") or not r.get("earliestStartTime")]
    print(f"Total races: {len(races)} | Missing key or time: {len(missing)}")

    updated, checked = update_rtrt_info(races, appid, token, dry_run=args.dry_run)
    print(f"Checked: {checked} | Updated: {updated}")

    if updated and not args.dry_run:
        save_races(RACES_PATH, races)
        print(f"Saved updates to {RACES_PATH}")
    elif updated and args.dry_run:
        print("Dry-run: not saving changes")
    else:
        print("No updates found")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
