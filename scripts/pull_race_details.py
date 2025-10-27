#!/usr/bin/env python3
"""
Pull race details for races from yesterday through the next 7 days.
Updates races.json with the latest category and split information from the API.
Creates backups when changes are detected.
"""

import json
import requests
import time
import os
import shutil
import sys
import argparse
from datetime import datetime, timedelta, date

# Get the directory containing this script
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
races_file = os.path.join(parent_dir, "races.json")
backup_dir = os.path.join(parent_dir, "backup")

# API configuration
CONF_URL = "https://api.rtrt.me/events/{}/conf"
CONF_PARAMS = {
    "appid": "5824c5c948fd08c23a8b4567",
    "token": "BB10EFF44090934C0EDC"
}

def is_valid_date_string(date_str: str) -> bool:
    """Return True if date_str is a non-empty YYYY-MM-DD date string."""
    if not isinstance(date_str, str) or not date_str:
        return False
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        return True
    except Exception:
        return False


def get_races_in_date_range(races_data, start_date: date, end_date: date):
    """Filter races to only include those happening between start_date and end_date (inclusive).

    Gracefully skips races with missing or invalid dates.
    """
    upcoming_races = []
    for race in races_data:
        race_date_str = race.get('date')
        if not is_valid_date_string(race_date_str):
            # Skip races with missing/invalid dates
            name = race.get('name', '<unknown>')
            print(f"WARNING: Skipping race with missing/invalid date: {name} (date={race_date_str})")
            continue
        try:
            race_date = datetime.strptime(race_date_str, '%Y-%m-%d').date()
        except Exception:
            # Extra safety; should have been caught above
            name = race.get('name', '<unknown>')
            print(f"WARNING: Skipping race due to unparsable date: {name} (date={race_date_str})")
            continue
        if start_date <= race_date <= end_date:
            upcoming_races.append(race)

    return upcoming_races

def extract_official_ag_urls(conf_data, race_distance):
    """Extract official age group URLs from the info section of conf data.

    Typical case: info item names don't include explicit distance; we match on 'Age Grad' and gender.
    Disambiguation case (multi-course): if multiple matches exist, prefer items whose name or link
    includes an indicator for the requested distance (70.3/703 for half; not-70.3 for full).
    """
    info_array = conf_data.get('conf', {}).get('info', [])

    # Pre-collect items with Age Grad marker
    items = []
    for info_item in info_array:
        name = (info_item.get('name') or "")
        link = (info_item.get('link') or "")
        if 'Age Grad' in name:
            items.append({"name": name, "link": link})

    if race_distance == "140.6":
        if not items:
            return ""
        if len(items) == 1:
            return items[0]["link"]

        # Disambiguate: prefer entries without 70.3 markers in name or link
        filtered = [it for it in items if ('70.3' not in it['name'] and '70.3' not in it['link'] and '703' not in it['link'])]
        if len(filtered) == 1:
            return filtered[0]['link']
        # Secondary preference: links that contain 'IRONMAN' (common full-distance suffix)
        prefer_ironman = [it for it in filtered if 'IRONMAN' in it['link']]
        if len(prefer_ironman) == 1:
            return prefer_ironman[0]['link']

        print("  WARNING: Multiple official AG URLs found for 140.6 race! This requires manual intervention.")
        return ""

    elif race_distance == "70.3":
        # Separate men and women
        men_all = [it for it in items if any(k in it['name'] for k in ['Men', 'Male'])]
        women_all = [it for it in items if any(k in it['name'] for k in ['Women', 'Female'])]

        def choose_for_half(cands, label):
            if not cands:
                return ""
            if len(cands) == 1:
                return cands[0]['link']
            # Prefer entries that indicate 70.3 in name or link
            filtered = [it for it in cands if ('70.3' in it['name'] or '70.3' in it['link'] or '703' in it['link'])]
            if len(filtered) == 1:
                return filtered[0]['link']
            print(f"  WARNING: Multiple official AG URLs found for {label} in 70.3 race! This requires manual intervention.")
            return ""

        men_url = choose_for_half(men_all, 'Men')
        women_url = choose_for_half(women_all, 'Women')
        return {"men": men_url, "women": women_url}

    # For other distances, return empty
    return ""


def choose_course_for_distance(conf_data: dict, race_distance: str) -> str | None:
    """Pick the correct course identifier from conf['skus']['reg'] for the given distance.

    Heuristics:
    - For 70.3, prefer entries whose 'race' or 'name' contains '70.3', else course id containing '703'.
    - For 140.6, prefer entries with 'race' containing 'IRONMAN' but not '70.3', else course id 'ironman' or not containing '703'.
    - Fallback: first available course or a sensible default ('ironman703'/'ironman').
    """
    reg = conf_data.get('conf', {}).get('skus', {}).get('reg', []) or []

    # Normalize course candidates
    candidates = []
    for entry in reg:
        candidates.append({
            "course": entry.get("course"),
            "race": entry.get("race") or entry.get("name") or entry.get("_id") or ""
        })

    if race_distance == "70.3":
        for c in candidates:
            if "70.3" in (c["race"] or "") and c.get("course"):
                return c["course"]
        for c in candidates:
            if c.get("course") and "703" in c["course"]:
                return c["course"]
        return "ironman703" if candidates else None

    if race_distance == "140.6":
        for c in candidates:
            name = c["race"] or ""
            if "IRONMAN" in name and "70.3" not in name and c.get("course"):
                return c["course"]
        for c in candidates:
            if c.get("course") == "ironman":
                return c["course"]
        for c in candidates:
            if c.get("course") and "703" not in c["course"]:
                return c["course"]
        return "ironman" if candidates else None

    # Unknown distance: return first available course if any
    return candidates[0].get("course") if candidates else None

def get_race_conf_data(race_id, race_distance):
    """Retrieve configuration data (categories, points, and official AG URLs) for a race from the /conf endpoint."""
    conf_url = CONF_URL.format(race_id)
    try:
        response = requests.get(conf_url, params=CONF_PARAMS)
        response.raise_for_status()
        conf_data = response.json()

        # Determine target course for this race distance
        target_course = choose_course_for_distance(conf_data, race_distance)

        # Extract categories from the conf data (filter to correct course if available)
        categories = conf_data.get('conf', {}).get('categories', [])
        if target_course:
            categories = [c for c in categories if c.get('course') == target_course]
        # Extract event date from conf
        conf_date = conf_data.get('conf', {}).get('date')
        # Extract earliest start time from conf (epoch seconds)
        conf_earliest_start = conf_data.get('conf', {}).get('earliestStartTime')
        # Normalize earliest start time to string if present
        if conf_earliest_start is not None:
            try:
                conf_earliest_start = str(int(conf_earliest_start))
            except Exception:
                # If it's not coercible to int, treat as absent
                conf_earliest_start = None

        # Find the relevant category names - check for duplicates
        men_cats = []
        women_cats = []

        for category in categories:
            title = category.get('title', '')
            subtitle = category.get('subtitle', '')
            # Look for Age Group categories with either "Overall" subtitle or empty subtitle
            if "Age Group Men" in title and (subtitle == "Overall" or subtitle == ""):
                men_cats.append(category['name'])
            elif "Age Group Women" in title and (subtitle == "Overall" or subtitle == ""):
                women_cats.append(category['name'])

        # Check for multiple age group categories
        if len(men_cats) > 1 or len(women_cats) > 1:
            return {
                "error": "multiple_categories",
                "categories": {"men_cat": "", "women_cat": ""},
                "split": None,
                "official_ag": ""
            }

        # Get single category names if found
        live_men_cat = men_cats[0] if men_cats else ""
        live_women_cat = women_cats[0] if women_cats else ""

        # Extract finish points from the vconf.pointorder data, filtered by course - check for duplicates
        point_order = conf_data.get('vconf', {}).get('pointorder', [])
        if target_course:
            point_order = [p for p in point_order if p.get('course') == target_course]
        finish_points = []

        for point in point_order:
            if point.get('isFinish') == '1':
                finish_points.append(point.get('name'))

        # Check for multiple finish points
        if len(finish_points) > 1:
            return {
                "error": "multiple_splits",
                "categories": {"men_cat": "", "women_cat": ""},
                "split": None,
                "official_ag": ""
            }

        # Only return split if it's not 'FINISH'
        split_name = None
        if finish_points and finish_points[0] != 'FINISH':
            split_name = finish_points[0]

        # Extract official AG URLs
        official_ag_urls = extract_official_ag_urls(conf_data, race_distance)

        return {
            "categories": {
                "men_cat": live_men_cat,
                "women_cat": live_women_cat
            },
            "split": split_name,
            "official_ag": official_ag_urls,
            "conf_date": conf_date,
            "earliestStartTime": conf_earliest_start
        }
    except Exception as e:
        print(f"Error retrieving conf data for {race_id}: {str(e)}")
        return {
            "error": "api_error",
            "categories": {
                "men_cat": "",
                "women_cat": ""
            },
            "split": None,
            "official_ag": "",
            "conf_date": None,
            "earliestStartTime": None
        }

def is_race_outdated(race):
    """Check if a race is outdated based on its distance and start time."""
    earliest_start_time = race.get('earliestStartTime')
    if not earliest_start_time:
        return False

    try:
        start_timestamp = int(earliest_start_time)
        start_datetime = datetime.fromtimestamp(start_timestamp)
        current_time = datetime.now()
        time_elapsed = current_time - start_datetime

        # Check thresholds based on distance
        distance = race.get('distance', '')
        if distance == "70.3":
            threshold_hours = 12
        elif distance == "140.6":
            threshold_hours = 24
        else:
            return False  # Don't warn for other distances

        return time_elapsed > timedelta(hours=threshold_hours)
    except (ValueError, TypeError):
        return False

def has_official_ag_results(race, conf_official_ag):
    """Check if race has official AG results in races.json or conf response."""
    # Check races.json first
    race_official_ag = race.get('results_urls', {}).get('official_ag', {})

    # For 70.3 races
    if race.get('distance') == "70.3":
        if isinstance(race_official_ag, dict):
            if race_official_ag.get('men') or race_official_ag.get('women'):
                return True
        if isinstance(conf_official_ag, dict):
            if conf_official_ag.get('men') or conf_official_ag.get('women'):
                return True

    # For 140.6 races
    elif race.get('distance') == "140.6":
        if isinstance(race_official_ag, str) and race_official_ag:
            return True
        if isinstance(conf_official_ag, str) and conf_official_ag:
            return True

    return False

def create_backup(races_file, backup_dir):
    """Create a backup of the races.json file with epoch timestamp."""
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)

    epoch_time = int(time.time())
    backup_filename = f"races.json.{epoch_time}"
    backup_path = os.path.join(backup_dir, backup_filename)

    shutil.copy2(races_file, backup_path)
    print(f"Created backup: {backup_path}")
    return backup_path

def parse_args():
    parser = argparse.ArgumentParser(description="Pull race details and update races.json from RTRT /conf")
    parser.add_argument("--dry-run", action="store_true", help="Do not write any changes or create backups")
    parser.add_argument("--race", dest="race_key", help="Race key to update only that race")
    parser.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD|yesterday", default=None,
                        help="Start date for selecting races (default: yesterday)")
    parser.add_argument("--to", dest="to_date", metavar="YYYY-MM-DD", default=None,
                        help="End date for selecting races (default: 7 days from today)")
    parser.add_argument(
        "--distance",
        dest="distance",
        metavar="{70.3|703|half|140.6|1406|full}",
        default=None,
        help=(
            "Optional distance filter. Accepts '70.3', '703', 'half' for 70.3 races, "
            "and '140.6', '1406', 'full' for 140.6 races."
        ),
    )
    return parser.parse_args()


def compute_date_range(from_date_str: str | None, to_date_str: str | None) -> tuple[date, date]:
    """Return (start_date, end_date) from optional CLI strings. Raises ValueError on bad input."""
    today = datetime.now().date()

    # Start date
    if from_date_str is None or from_date_str == "yesterday":
        start_date = today - timedelta(days=1)
    else:
        if not is_valid_date_string(from_date_str):
            raise ValueError(f"--from must be 'yesterday' or YYYY-MM-DD, got '{from_date_str}'")
        start_date = datetime.strptime(from_date_str, "%Y-%m-%d").date()

    # End date
    if to_date_str is None:
        end_date = today + timedelta(days=7)
    else:
        if not is_valid_date_string(to_date_str):
            raise ValueError(f"--to must be YYYY-MM-DD, got '{to_date_str}'")
        end_date = datetime.strptime(to_date_str, "%Y-%m-%d").date()

    if start_date > end_date:
        raise ValueError(f"--from date {start_date} cannot be after --to date {end_date}")

    return start_date, end_date


def normalize_distance_filter(dist: str | None) -> str | None:
    """Normalize a user-supplied distance string to '70.3' or '140.6'.

    Returns None if dist is None. Raises ValueError if provided but unrecognized.
    """
    if dist is None:
        return None
    v = str(dist).strip().lower()
    if v in {"70.3", "703", "half"}:
        return "70.3"
    if v in {"140.6", "1406", "full"}:
        return "140.6"
    raise ValueError(
        f"Unrecognized --distance value '{dist}'. Use one of: 70.3, 703, half, 140.6, 1406, full."
    )


def main():
    args = parse_args()
    # Normalize optional distance filter early
    try:
        distance_filter = normalize_distance_filter(args.distance)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(2)

    # Read the races.json file
    if not os.path.exists(races_file):
        print(f"Error: {races_file} not found")
        sys.exit(1)

    with open(races_file, 'r', encoding='utf-8') as f:
        races_data = json.load(f)

    # Loudly warn if any race is missing a valid date or a key
    missing_count = 0
    for race in races_data:
        name = race.get('name', '<unknown>')
        has_valid_date = is_valid_date_string(race.get('date'))
        has_key = bool(race.get('key'))
        if not has_valid_date or not has_key:
            missing_count += 1
            parts = []
            if not has_valid_date:
                parts.append('date')
            if not has_key:
                parts.append('key')
            fields = ' and '.join(parts)
            print(f"\n⚠️  WARNING: Race missing {fields.upper()}: {name} (date={race.get('date')}, key={race.get('key')})\n")

    # Determine which races to process
    if args.race_key:
        # Error if date range options also provided
        if args.from_date is not None or args.to_date is not None:
            print("Error: --race cannot be used together with --from or --to")
            sys.exit(2)

        # Gather all races matching the key; optionally filter by distance
        key_matches = [r for r in races_data if r.get('key') == args.race_key]
        if not key_matches:
            print(f"Error: Race with key '{args.race_key}' not found in races.json")
            sys.exit(2)

        if distance_filter is not None:
            key_matches = [r for r in key_matches if r.get('distance') == distance_filter]
            if not key_matches:
                # Provide helpful hint about available distances for this key
                available = sorted({r.get('distance') for r in races_data if r.get('key') == args.race_key})
                print(
                    f"Error: No race with key '{args.race_key}' and distance '{distance_filter}'. "
                    f"Available distances for this key: {', '.join(available) if available else 'unknown'}"
                )
                sys.exit(2)

        # If multiple remain and no distance filter, inform and proceed with first
        if len(key_matches) > 1 and distance_filter is None:
            distances = ", ".join(sorted({r.get('distance') for r in key_matches}))
            print(
                f"Note: Multiple races share key '{args.race_key}' (distances: {distances}). "
                f"Use --distance to disambiguate. Proceeding with the first match."
            )

        selected = key_matches[0]
        upcoming_races = [selected]
        print(f"Found {len(upcoming_races)} race(s) by key:")
        print(f"  - {selected.get('name')} ({selected.get('date')})")
    else:
        try:
            start_date, end_date = compute_date_range(args.from_date, args.to_date)
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(2)

        upcoming_races = get_races_in_date_range(races_data, start_date, end_date)

        # Apply optional distance filter
        if distance_filter is not None:
            upcoming_races = [r for r in upcoming_races if r.get('distance') == distance_filter]

        if not upcoming_races:
            print(f"No races found between {start_date} and {end_date}")
            return

        print(f"Found {len(upcoming_races)} race(s) between {start_date} and {end_date}:")
        for race in upcoming_races:
            print(f"  - {race.get('name')} ({race.get('date')})")

    changes_made = False

    # Process each upcoming race
    for race in upcoming_races:
        race_id = race.get('key', race['name'])  # Use key if available, otherwise name
        print(f"\nProcessing {race['name']} (ID: {race_id})...")

        # Get current live categories from race data
        current_live = race.get('results_urls', {}).get('live', {})
        current_men_cat = current_live.get('men_cat', '')
        current_women_cat = current_live.get('women_cat', '')
        current_split = race.get('split', None)
        current_date = race.get('date')

        # Get current official_ag data
        race_distance = race.get('distance', '')
        current_official_ag = race.get('results_urls', {}).get('official_ag', {})

        # Retrieve new configuration data (categories, split info, and official AG URLs)
        conf_data = get_race_conf_data(race_id, race_distance)

        # Check if race is outdated and lacks official AG results
        if is_race_outdated(race) and not has_official_ag_results(race, conf_data.get('official_ag', {})):
            distance = race.get('distance', '')
            threshold_hours = 12 if distance == "70.3" else 24
            print(f"  ⚠️  WARNING: Race start time is more than {threshold_hours} hours in the past and no official AG results are available!")

        # Check for error conditions that require manual intervention
        if 'error' in conf_data:
            if conf_data['error'] == 'multiple_splits':
                print(f"  WARNING: This race has multiple isFinish splits! Skipping automatic updates.")
                continue
            elif conf_data['error'] == 'multiple_categories':
                print(f"  WARNING: This race has multiple Top Age Group Overall categories! Skipping automatic updates.")
                continue
            elif conf_data['error'] == 'api_error':
                print(f"  WARNING: API error occurred. Skipping this race.")
                continue

        # Use safe accessors for robustness if keys are missing
        new_categories = conf_data.get('categories', {"men_cat": "", "women_cat": ""})
        new_split = conf_data.get('split', None)
        new_official_ag = conf_data.get('official_ag', {})
        new_earliest = conf_data.get('earliestStartTime')

        # Confirm the date aligns between /conf and races.json; if not, update races.json
        new_date = conf_data.get('conf_date')
        date_changed = False
        if new_date and not is_valid_date_string(new_date):
            # Treat invalid/empty dates from the API as absent; don't store empty/null dates
            print(f"  ⚠️  WARNING: /conf returned invalid/empty date '{new_date}', ignoring date update.")
            new_date = None
        if new_date and current_date != new_date:
            print(f"  ⚠️  WARNING: Date mismatch: /conf has {new_date}, races.json has {current_date}")
            date_changed = True

        # Check if categories need updating
        categories_changed = False
        if (new_categories['men_cat'] != current_men_cat or
            new_categories['women_cat'] != current_women_cat):
            categories_changed = True
            print(f"  Categories changed:")
            print(f"    Men: '{current_men_cat}' -> '{new_categories['men_cat']}'")
            print(f"    Women: '{current_women_cat}' -> '{new_categories['women_cat']}'")

        # Check if split needs updating
        split_changed = False
        if new_split != current_split:
            split_changed = True
            print(f"  Split changed: '{current_split}' -> '{new_split}'")

        # Check if official_ag needs updating
        official_ag_changed = False
        if race_distance == "140.6":
            # For 140.6 races, compare single URL
            current_ag_url = current_official_ag if isinstance(current_official_ag, str) else ""
            if new_official_ag != current_ag_url and new_official_ag:
                official_ag_changed = True
                print(f"  Official AG URL changed: '{current_ag_url}' -> '{new_official_ag}'")
        elif race_distance == "70.3":
            # For 70.3 races, compare men and women URLs
            current_men_ag = current_official_ag.get('men', '') if isinstance(current_official_ag, dict) else ""
            current_women_ag = current_official_ag.get('women', '') if isinstance(current_official_ag, dict) else ""
            new_men_ag = new_official_ag.get('men', '') if isinstance(new_official_ag, dict) else ""
            new_women_ag = new_official_ag.get('women', '') if isinstance(new_official_ag, dict) else ""

            if ((new_men_ag != current_men_ag and new_men_ag) or
                (new_women_ag != current_women_ag and new_women_ag)):
                official_ag_changed = True
                print(f"  Official AG URLs changed:")
                if new_men_ag != current_men_ag and new_men_ag:
                    print(f"    Men: '{current_men_ag}' -> '{new_men_ag}'")
                if new_women_ag != current_women_ag and new_women_ag:
                    print(f"    Women: '{current_women_ag}' -> '{new_women_ag}'")

        # Check if earliestStartTime needs updating (only if API returned a value)
        earliest_changed = False
        if new_earliest:
            current_earliest = race.get('earliestStartTime')
            # Normalize current to string for fair comparison
            current_earliest_str = str(current_earliest) if current_earliest is not None else None
            if new_earliest != current_earliest_str:
                earliest_changed = True
                print(f"  earliestStartTime changed: '{current_earliest_str}' -> '{new_earliest}'")

        # Update the race data if changes detected
        if categories_changed or split_changed or official_ag_changed or date_changed or earliest_changed:
            changes_made = True

            # Ensure results_urls structure exists
            if 'results_urls' not in race:
                race['results_urls'] = {}
            if 'live' not in race['results_urls']:
                race['results_urls']['live'] = {}

            # Update categories
            race['results_urls']['live']['men_cat'] = new_categories['men_cat']
            race['results_urls']['live']['women_cat'] = new_categories['women_cat']

            # Update split
            if new_split is not None:
                race['split'] = new_split
            elif 'split' in race and current_split is not None:
                # Remove split if it's no longer needed
                del race['split']

            # Update official_ag URLs
            if official_ag_changed and new_official_ag:
                race['results_urls']['official_ag'] = new_official_ag

            # Update date if changed and valid; never store an empty or invalid date
            if new_date and is_valid_date_string(new_date):
                race['date'] = new_date
            else:
                # If we don't have a valid new date, don't overwrite whatever is there
                pass

            # Update earliestStartTime only if we received a value and it's different
            if earliest_changed and new_earliest:
                race['earliestStartTime'] = new_earliest

        # Small delay to be nice to the API
        time.sleep(0.5)

    # Create backup and save changes if any were made
    if changes_made:
        if args.dry_run:
            print("\nDry run: changes detected, but not writing to races.json or creating a backup.")
        else:
            print(f"\nChanges detected, creating backup...")
            create_backup(races_file, backup_dir)

            print(f"Updating {races_file}...")
            with open(races_file, 'w', encoding='utf-8') as f:
                json.dump(races_data, f, indent=4, ensure_ascii=False)

            print("Update complete!")
    else:
        print("\nNo changes detected, no backup or update needed.")

if __name__ == "__main__":
    main()