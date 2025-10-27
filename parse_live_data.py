import re
from datetime import timedelta
import requests
import json
import math
from flask import current_app
import cache_utils as cache

# The common parameters for the RTRT.me live API
RTRT_LIVE_PARAMS = {
    "timesort": "1",
    "nohide": "1",
    "checksum": "",
    "appid": "5824c5c948fd08c23a8b4567",
    "token": "BB10EFF44090934C0EDC",
    "max": "2000",
    "catloc": "1",
    "cattotal": "1",
    "units": "standard",
    "source": "webtracker"
}

def time_to_seconds(time_str):
    """
    Converts a time string in "HH:MM:SS" format to total seconds (float).
    """
    match = re.match(r'(\d+):(\d+):(\d+)', time_str)
    if not match:
        return None

    hours, minutes, seconds = map(int, match.groups())
    total_seconds = (hours * 3600) + (minutes * 60) + seconds
    return total_seconds

def seconds_to_time(total_seconds):
    """
    Converts a total number of seconds (float) to "HH:MM:SS" format.
    """
    if total_seconds is None or total_seconds < 0:
        return ""

    td = timedelta(seconds=total_seconds)
    hours, remainder = divmod(td.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def fetch_live_results(api_url):
    """
    Fetches the raw JSON data from the RTRT.me live API.
    """
    try:
        current_app.logger.debug(f"Making POST request to {api_url} with params: {RTRT_LIVE_PARAMS}")
        response = requests.post(api_url, data=RTRT_LIVE_PARAMS)
        response.raise_for_status()
        raw_data = response.json()
        current_app.logger.debug(f"Received response from {api_url}: Status {response.status_code}")

        # Handle the specific "no_results" error
        if "error" in raw_data and raw_data["error"]["type"] == "no_results":
            return {"error": "no_finishers"}

        return raw_data
    except requests.exceptions.RequestException as e:
        current_app.logger.error(f"Error retrieving data from {api_url}: {e}")
        return {"error": f"Error retrieving data: {str(e)}"}
    except (json.JSONDecodeError, KeyError) as e:
        current_app.logger.error(f"Error parsing JSON data: {e}")
        return {"error": f"Error parsing JSON data: {str(e)}"}

def process_live_results(raw_data_list, ag_adjustments):
    """
    Parses, grades, and sorts a list of raw athlete data.
    """
    if not isinstance(raw_data_list, list):
        return {"error": "Invalid data format provided for processing."}

    # Accept only standard age-group divisions like M18-24 or F45-49 (with or without hyphen)
    ag_pattern = re.compile(r'^([MF])(\d{2})-?(\d{2})$', re.IGNORECASE)

    processed_data = []
    for item in raw_data_list:
        try:
            # Use only the time without fractional seconds -- that's too much detail
            finish_time_str_raw = item.get("time", "").split(".")[0]
            finish_time_seconds = time_to_seconds(finish_time_str_raw)

            if finish_time_seconds is None:
                continue

            # Filter to valid AG divisions only and normalize to e.g., M18-24/F45-49
            raw_division = item.get("division", "") or ""
            division_compact = re.sub(r"\s+", "", raw_division)
            m = ag_pattern.match(division_compact)
            if not m:
                # Skip non-eligible (e.g., Pro, Relay, etc.)
                continue
            sex, a, b = m.group(1).upper(), m.group(2), m.group(3)
            age_group = f"{sex}{a}-{b}"

            ag_adjustment = ag_adjustments.get(age_group, 1.0)
            graded_seconds = finish_time_seconds * ag_adjustment

            processed_data.append({
                "bib": item.get("bib", "N/A"),
                "name": item.get("name", "N/A"),
                "age_group": age_group,
                "finish_time": finish_time_str_raw,
                "finish_time_seconds": finish_time_seconds,
                "gender_place": item.get("place", "N/A"),
                "graded_time_seconds": graded_seconds,
                "graded_time": seconds_to_time(graded_seconds),
                "unique_key": (item.get("name"), finish_time_str_raw)
            })
        except (KeyError, TypeError) as e:
            current_app.logger.warning(f"Error parsing athlete data: {e} - Skipping entry.")
            continue

    processed_data.sort(key=lambda x: x["graded_time_seconds"])

    if processed_data:
        # Initialize first athlete's overall graded position
        processed_data[0]["graded_place"] = 1

        # Trackers for age-group places as we iterate the graded list
        ag_place_counters = {}

        # Assign AG place for the first athlete
        first_ag = processed_data[0].get("age_group", "N/A")
        ag_place_counters[first_ag] = 1
        processed_data[0]["ag_place"] = 1

        # Handle all other athletes
        for i in range(1, len(processed_data)):
            # Overall graded place with tie handling
            if processed_data[i]["graded_time_seconds"] == processed_data[i-1]["graded_time_seconds"]:
                # Same time as previous athlete, assign same position
                processed_data[i]["graded_place"] = processed_data[i-1]["graded_place"]
            else:
                # Different time, position should be i+1 to account for previous ties
                # For example: if we had positions [1,1,1], the next unique time should be position 4
                processed_data[i]["graded_place"] = i + 1

            # Age-group place tracking: increment per age group in the graded order
            ag = processed_data[i].get("age_group", "N/A")
            ag_place_counters[ag] = ag_place_counters.get(ag, 0) + 1
            processed_data[i]["ag_place"] = ag_place_counters[ag]

    return processed_data

def get_processed_results(race, gender, ag_adjustments):
    """
    Unified function to fetch and process results based on race distance and gender.
    """
    all_data_list = []
    if race['distance'] == '70.3':
        api_url = race['results_urls']['live'].get(gender)
        raw_data = fetch_live_results(api_url)
        if "error" in raw_data:
            return raw_data
        all_data_list.extend(raw_data.get("list", []))

    elif race['distance'] == '140.6':
        men_url = race['results_urls']['live'].get('men')
        women_url = race['results_urls']['live'].get('women')

        men_data = fetch_live_results(men_url)
        women_data = fetch_live_results(women_url)

        if "error" in men_data and "error" in women_data:
            return men_data
        elif "error" in men_data:
            current_app.logger.warning(f"Error fetching men's live results from {men_url}: {men_data['error']}")
        elif "error" in women_data:
            current_app.logger.warning(f"Error fetching women's live results from {women_url}: {women_data['error']}")

        all_data_list.extend(men_data.get("list", []))
        all_data_list.extend(women_data.get("list", []))

    else:
        return {"error": f"Invalid race distance: {race['distance']}"}

    if not all_data_list:
        return {"error": "No live data found for the selected race."}

    return process_live_results(all_data_list, ag_adjustments)


def get_processed_results_cached(race, gender, ag_adjustments):
    """
    Fetch and process results with caching semantics:
    - If official_ag exists and final cache is missing: fetch, process, store in final, return.
    - If final cache exists: return it without hitting network.
    - Else (no official_ag or no final):
        * If in_progress cache exists and is fresh: return it.
        * Otherwise: fetch, process, store to in_progress, return.
    Freshness window configurable via app config 'CACHE_FRESHNESS_SECONDS' (default 60).
    """
    freshness = int(current_app.config.get('CACHE_FRESHNESS_SECONDS', 60))

    distance = race.get('distance')
    # Normalize gender for 70.3; ignored for 140.6
    effective_gender = None
    if distance == '70.3':
        effective_gender = gender or 'men'

    has_official = cache.has_official_ag(race)

    # Determine cache paths
    final_path = cache.get_cache_file_path(race, 'final', effective_gender)
    inprog_path = cache.get_cache_file_path(race, 'in_progress', effective_gender)

    # If official AG and we already have a final cache, use it
    if has_official:
        data = cache.read_json_if_exists(final_path)
        if data is not None:
            current_app.logger.debug(f"Serving results from FINAL cache: {final_path}")
            return data

        # No final cache yet; fetch and persist to final
        current_app.logger.info(f"FINAL cache missing, fetching live to build FINAL for {race.get('key')}")
        data = get_processed_results(race, effective_gender, ag_adjustments)
        if isinstance(data, dict) and 'error' in data:
            return data
        cache.write_json(final_path, data)
        return data

    # No official AG or no final path desired; try in-progress cache first
    if cache.is_fresh(inprog_path, freshness):
        data = cache.read_json_if_exists(inprog_path)
        if data is not None:
            current_app.logger.debug(f"Serving results from IN_PROGRESS cache: {inprog_path}")
            return data

    # Not fresh or not present; fetch and update in_progress
    current_app.logger.debug(f"Fetching live results for {race.get('key')} (updating IN_PROGRESS cache)")
    data = get_processed_results(race, effective_gender, ag_adjustments)
    if not (isinstance(data, dict) and 'error' in data):
        cache.write_json(inprog_path, data)
    return data