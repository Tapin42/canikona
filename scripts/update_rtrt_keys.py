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

# ANSI SGR codes (only used when stdout is a TTY)
SGR = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "yellow": "\033[33m",
    "green": "\033[32m",
    "cyan": "\033[36m",
}


def _use_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _fmt(text: str, *codes: str) -> str:
    if not _use_color():
        return text
    return "".join(SGR.get(c, "") for c in codes) + text + SGR["reset"]


# Unicode box-drawing for tables
_BOX = {
    "tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
    "hl": "─", "vl": "│",
    "tt": "┬", "bt": "┴", "lt": "├", "rt": "┤", "ct": "┼",
}


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
    backup_path.write_text(json.dumps(load_races(path), ensure_ascii=False, indent=4))
    path.write_text(json.dumps(races, ensure_ascii=False, indent=4))


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
    # Remove distance markers (distance is a separate field in races.json)
    # Handle common spellings like "70.3", "70 3", "140.6", etc.
    base = re.sub(r"\b70\W*3\b", " ", base)
    base = re.sub(r"\b140\W*6\b", " ", base)
    base = base.replace("703", " ")
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
    # First token is often the main location (e.g. Texas, Wisconsin);
    # RTRT keys commonly use just the location, e.g. IRM-TEXAS-2026
    if len(tokens) >= 2:
        variants.append(tokens[0])
    # First two tokens handle two-word locations (e.g. South Africa -> SOUTHAFRICA)
    if len(tokens) >= 2:
        variants.append("".join(tokens[:2]))
        variants.append("-".join(tokens[:2]))
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


def _extract_events_list(payload) -> List[Dict]:
    """Extract event list from API response payload."""
    if isinstance(payload, list):
        return [e for e in payload if isinstance(e, dict)]
    if isinstance(payload, dict):
        for k in ("list", "data", "events", "items"):
            v = payload.get(k)
            if isinstance(v, list):
                return [e for e in v if isinstance(e, dict)]
    return []


def _event_date_in_past(event: Dict, today: str) -> bool:
    """Return True if the event's date is strictly before today."""
    evt_date = event.get("date") or event.get("startDate")
    if not isinstance(evt_date, str) or len(evt_date) < 10:
        return False
    day = evt_date[:10]
    return day < today


def rtrt_list_events_index(
    appid: str,
    token: str,
    *,
    fields: str = "name,date,desc,earliestStartTime,url",
    page_size: int = 50,
) -> List[Dict]:
    """Fetch a lightweight events index from RTRT with pagination.

    This endpoint can expose event keys even when full event details for a key
    are missing or not yet published.

    The API returns events reverse-time sorted (newest first). We paginate with
    a small page size and stop as soon as we see an event with a date in the
    past, since we never need to process races that have already happened.
    """
    url = "https://api.rtrt.me/events"
    today = datetime.now().strftime("%Y-%m-%d")
    all_events: List[Dict] = []
    next_start = 1
    max_pages = 500  # safety limit

    for _ in range(max_pages):
        try:
            r = requests.get(
                url,
                params={
                    "appid": appid,
                    "token": token,
                    "fields": fields,
                    "max": page_size,
                    "start": next_start,
                },
                timeout=20,
            )
            if r.status_code != 200:
                break
            payload = r.json()
        except (requests.RequestException, ValueError):
            break

        events = _extract_events_list(payload)
        if not events:
            break

        info = payload.get("info", {}) if isinstance(payload, dict) else {}
        try:
            last_i = int(info.get("last", 0))
        except (TypeError, ValueError):
            last_i = 0

        for e in events:
            if _event_date_in_past(e, today):
                # Reached past events; stop paginating. We don't need them.
                return all_events
            all_events.append(e)

        # Check if we've reached the last page
        window = last_i - next_start + 1 if last_i >= next_start else 0
        if window < page_size:
            break

        next_start = last_i + 1
        time.sleep(0.2)  # be polite to the API

    return all_events


def event_key_from_index_row(event: Dict) -> Optional[str]:
    for k in ("key", "eventKey", "event_key", "id", "eventId", "name"):
        v = event.get(k)
        if isinstance(v, str) and v:
            # In the /events index, `name` is commonly the event key (e.g. IRM-FOO-2026)
            if k == "name":
                if v.upper().startswith("IRM-"):
                    return v
                # If `name` isn't a key-looking string, ignore it.
                continue
            return v

    # Try to infer from URLs like https://api.rtrt.me/events/irm-foo-2026
    for k in ("url", "href", "self"):
        v = event.get(k)
        if isinstance(v, str) and v:
            m = re.search(r"/events/([^/?#]+)", v)
            if m:
                return m.group(1)
    return None


def event_distance_from_key_or_name(key: Optional[str], name: str) -> Optional[str]:
    k = (key or "").upper()
    n = name.upper()
    if "703" in k or "70.3" in n or "70 3" in n:
        return "70.3"
    if "140.6" in n or "140 6" in n:
        return "140.6"
    return None


def build_events_by_date(events: List[Dict]) -> Dict[str, List[Dict]]:
    out: Dict[str, List[Dict]] = {}
    for e in events:
        date = e.get("date") or e.get("startDate")
        if not isinstance(date, str) or not date:
            continue
        day = date[:10]
        out.setdefault(day, []).append(e)
    return out


def dates_match(race_date: str, event: Dict) -> bool:
    # RTRT event JSON has fields like 'date' or nested; accept prefix match
    evt_date = event.get("date") or event.get("startDate") or ""
    return isinstance(evt_date, str) and evt_date.startswith(race_date)


def update_rtrt_info(races: List[Dict], appid: str, token: str, dry_run: bool = False) -> Tuple[int, int, List[Tuple[str, str, str]]]:
    # Fetch lightweight index once; used as a fallback to assign keys even when
    # full event details for a key are missing.
    events_index = rtrt_list_events_index(appid, token)
    events_by_date = build_events_by_date(events_index)

    events_by_key: Dict[str, Dict] = {}
    for e in events_index:
        k = event_key_from_index_row(e)
        if isinstance(k, str) and k:
            events_by_key[k.upper()] = e

    updated = 0
    checked = 0
    newly_matched: List[Tuple[str, str, str]] = []  # (date, name, key)
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

        # If key is missing, try to assign it from the events index first.
        # This can succeed even when /events/{key} is unavailable.
        if not has_key:
            day_events = events_by_date.get(date, [])

            # Prefer exact candidate match against keys in the index.
            day_keys: Dict[str, Dict] = {}
            for e in day_events:
                k = event_key_from_index_row(e)
                if isinstance(k, str) and k:
                    day_keys[k.upper()] = e

            matched_row: Optional[Dict] = None
            matched_key: Optional[str] = None
            for cand in make_candidate_keys(name, year, distance):
                row = day_keys.get(cand.upper())
                if row:
                    matched_row = row
                    matched_key = cand
                    break

            # If the race date in races.json doesn't match RTRT's index date,
            # still allow exact candidate-key matches anywhere in the index.
            if not matched_key:
                for cand in make_candidate_keys(name, year, distance):
                    row = events_by_key.get(cand.upper())
                    if row:
                        matched_row = row
                        matched_key = cand
                        break

            # Fallback: fuzzy match by normalized name variants (only if unique).
            if not matched_key and day_events:
                race_variants = set(build_name_variants(name))
                candidates: List[Tuple[str, Dict]] = []
                for e in day_events:
                    ek = event_key_from_index_row(e)
                    # In the /events index, `desc` is the human-readable event name.
                    en = e.get("desc") or e.get("eventName") or e.get("description") or ""
                    if not isinstance(en, str):
                        en = ""
                    if not isinstance(ek, str) or not ek:
                        continue
                    ed = event_distance_from_key_or_name(ek, en)
                    if ed and str(ed) != str(distance):
                        continue
                    ev_variants = set(build_name_variants(en))
                    if race_variants.intersection(ev_variants):
                        candidates.append((ek, e))

                if len(candidates) == 1:
                    matched_key, matched_row = candidates[0]

            if matched_key:
                race["key"] = matched_key
                has_key = True
                if not has_time and matched_row:
                    est = matched_row.get("earliestStartTime") or matched_row.get("startTime")
                    if est:
                        race["earliestStartTime"] = str(est)
                        has_time = True
                updated += 1
                newly_matched.append((date or "", name, matched_key))

        # If we still don't have a key, fall through to the older inference+validation.

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
            newly_matched.append((date or "", name, cand))
            break

    return updated, checked, newly_matched


def _truncate(s: str, max_len: int) -> str:
    """Truncate string with ellipsis if needed."""
    s = str(s)
    return (s[: max_len - 1] + "…") if len(s) > max_len else s


def _print_table(
    title: str,
    headers: List[str],
    rows: List[Tuple[str, ...]],
    title_style: str = "bold",
) -> None:
    """Print a box-drawn table with optional ANSI styling."""
    max_col = 52
    widths = [min(len(h), max_col) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], min(len(str(cell)), max_col))
    top = _BOX["tl"] + _BOX["tt"].join(_BOX["hl"] * (w + 2) for w in widths) + _BOX["tr"]
    sep = _BOX["lt"] + _BOX["ct"].join(_BOX["hl"] * (w + 2) for w in widths) + _BOX["rt"]
    bot = _BOX["bl"] + _BOX["bt"].join(_BOX["hl"] * (w + 2) for w in widths) + _BOX["br"]

    def row_fmt(cells: Tuple[str, ...]) -> str:
        parts = []
        for i, c in enumerate(cells):
            w = widths[i] if i < len(widths) else 10
            parts.append(_truncate(c, w).ljust(w))
        return _BOX["vl"] + _BOX["vl"].join(f" {p} " for p in parts) + _BOX["vl"]

    print()
    print(_fmt(title, title_style))
    print(_fmt(top, "dim"))
    print(_fmt(row_fmt(tuple(headers)), "dim", "bold"))
    print(_fmt(sep, "dim"))
    for r in rows:
        print(row_fmt(r))
    print(_fmt(bot, "dim"))


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

    updated, checked, newly_matched = update_rtrt_info(races, appid, token, dry_run=args.dry_run)
    print(f"Checked: {checked} | Updated: {updated}")

    if updated and not args.dry_run:
        save_races(RACES_PATH, races)
        print(f"Saved updates to {RACES_PATH}")
    elif updated and args.dry_run:
        print("Dry-run: not saving changes")
    else:
        print("No updates found")

    # Table 1: Races still missing keys (after any updates)
    still_missing_key = [r for r in races if not r.get("key")]
    if still_missing_key:
        _print_table(
            "Races still missing keys",
            ["Date", "Name"],
            [(r.get("date", ""), r.get("name", "")) for r in still_missing_key],
            title_style="yellow",
        )

    # Table 2: Races newly matched this run
    if newly_matched:
        _print_table(
            "Newly matched this run",
            ["Date", "Name", "Key"],
            newly_matched,
            title_style="green",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
