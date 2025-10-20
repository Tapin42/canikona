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
from datetime import datetime, timedelta

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

def get_races_in_next_7_days(races_data):
    """Filter races to only include those happening from yesterday through the next 7 days."""
    today = datetime.now().date()
    start_date = today - timedelta(days=1)  # Yesterday
    end_date = today + timedelta(days=7)

    upcoming_races = []
    for race in races_data:
        race_date = datetime.strptime(race['date'], '%Y-%m-%d').date()
        if start_date <= race_date <= end_date:
            upcoming_races.append(race)

    return upcoming_races

def extract_official_ag_urls(conf_data, race_distance):
    """Extract official age group URLs from the info section of conf data."""
    info_array = conf_data.get('conf', {}).get('info', [])

    if race_distance == "140.6":
        # For full Ironman, look for a single "Age Graded" entry
        found = ""
        for info_item in info_array:
            name = info_item.get('name', '')
            if 'Age Grad' in name:
                if not found:
                    found = info_item.get('link', '')
                else: # Multiple found, warn and return empty
                    print("  WARNING: Multiple official AG URLs found for 140.6 race! This requires manual intervention.")
                    return ""

        return found

    elif race_distance == "70.3":
        # For 70.3, look for separate Men and Women entries
        men_url = ""
        women_url = ""

        for info_item in info_array:
            name = info_item.get('name', '')
            if 'Age Grad' in name:
                # Check if this is for men or women
                if any(keyword in name for keyword in ['Men', 'Male']):
                    if not men_url:
                        men_url = info_item.get('link', '')
                    else: # Multiple found, warn and return empty
                        print("  WARNING: Multiple official AG URLs found for Men in 70.3 race! This requires manual intervention.")
                        return {"men": "", "women": ""}
                elif any(keyword in name for keyword in ['Women', 'Female']):
                    if not women_url:
                        women_url = info_item.get('link', '')
                    else: # Multiple found, warn and return empty
                        print("  WARNING: Multiple official AG URLs found for Women in 70.3 race! This requires manual intervention.")
                        return {"men": "", "women": ""}
        return {"men": men_url, "women": women_url}

    # For other distances, return empty
    return ""

def get_race_conf_data(race_id, race_distance):
    """Retrieve configuration data (categories, points, and official AG URLs) for a race from the /conf endpoint."""
    conf_url = CONF_URL.format(race_id)
    try:
        response = requests.get(conf_url, params=CONF_PARAMS)
        response.raise_for_status()
        conf_data = response.json()

        # Extract categories from the conf data
        categories = conf_data.get('conf', {}).get('categories', [])
        # Extract event date from conf
        conf_date = conf_data.get('conf', {}).get('date')

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

        # Extract finish points from the vconf.pointorder data - check for duplicates
        point_order = conf_data.get('vconf', {}).get('pointorder', [])
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
            "conf_date": conf_date
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
            "conf_date": None
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

def main():
    # Read the races.json file
    if not os.path.exists(races_file):
        print(f"Error: {races_file} not found")
        return

    with open(races_file, 'r', encoding='utf-8') as f:
        races_data = json.load(f)

    # Filter races from yesterday through next 7 days
    upcoming_races = get_races_in_next_7_days(races_data)

    if not upcoming_races:
        print("No races found from yesterday through the next 7 days")
        return

    print(f"Found {len(upcoming_races)} race(s) from yesterday through the next 7 days:")
    for race in upcoming_races:
        print(f"  - {race['name']} ({race['date']})")

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

        new_categories = conf_data['categories']
        new_split = conf_data['split']
        new_official_ag = conf_data['official_ag']

        # Confirm the date aligns between /conf and races.json; if not, update races.json
        new_date = conf_data.get('conf_date')
        date_changed = False
        if current_date != new_date:
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

        # Update the race data if changes detected
        if categories_changed or split_changed or official_ag_changed or date_changed:
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

            # Update date if changed
            race['date'] = new_date

        # Small delay to be nice to the API
        time.sleep(0.5)

    # Create backup and save changes if any were made
    if changes_made:
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