import re
from datetime import timedelta
import requests
import json
import math

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
        response = requests.post(api_url, data=RTRT_LIVE_PARAMS)
        response.raise_for_status()
        raw_data = response.json()

        # Handle the specific "no_results" error
        if "error" in raw_data and raw_data["error"]["type"] == "no_results":
            return {"error": "no_finishers", "msg": "No racers have crossed the finish line yet. The age-graded results will start populating once racers start finishing the race."}

        return raw_data
    except requests.exceptions.RequestException as e:
        print(f"Error retrieving data from {api_url}: {e}")
        return {"error": f"Error retrieving data: {str(e)}"}
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Error parsing JSON data: {e}")
        return {"error": f"Error parsing JSON data: {str(e)}"}

def process_live_results(raw_data_list, ag_adjustments):
    """
    Parses, grades, and sorts a list of raw athlete data.
    """
    if not isinstance(raw_data_list, list):
        return {"error": "Invalid data format provided for processing."}

    processed_data = []
    for item in raw_data_list:
        try:
            # Use only the time without fractional seconds -- that's too much detail
            finish_time_str_raw = item.get("time", "").split(".")[0]
            finish_time_seconds = time_to_seconds(finish_time_str_raw)

            if finish_time_seconds is None:
                continue

            age_group = item.get("division", "N/A")
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
            print(f"Error parsing athlete data: {e} - Skipping entry.")
            continue

    processed_data.sort(key=lambda x: x["graded_time_seconds"])

    if processed_data:
        # Initialize first athlete's position
        processed_data[0]["graded_place"] = 1

        # Handle all other athletes
        for i in range(1, len(processed_data)):
            if processed_data[i]["graded_time_seconds"] == processed_data[i-1]["graded_time_seconds"]:
                # Same time as previous athlete, assign same position
                processed_data[i]["graded_place"] = processed_data[i-1]["graded_place"]
            else:
                # Different time, position should be i+1 to account for previous ties
                # For example: if we had positions [1,1,1], the next unique time should be position 4
                processed_data[i]["graded_place"] = i + 1

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
        if "error" in men_data:
            return men_data

        women_data = fetch_live_results(women_url)
        if "error" in women_data:
            return women_data

        all_data_list.extend(men_data.get("list", []))
        all_data_list.extend(women_data.get("list", []))

    else:
        return {"error": f"Invalid race distance: {race['distance']}"}

    if not all_data_list:
        return {"error": "No live data found for the selected race."}

    return process_live_results(all_data_list, ag_adjustments)