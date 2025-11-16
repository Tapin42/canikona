import re
from datetime import datetime, timedelta
import requests
import json
import math
from flask import current_app
import cache_utils as cache

# ---------------------------------------------------------------------------
# Dynamic slot persistence cache
# ---------------------------------------------------------------------------
# We persist computed dynamic slot allocations and started_counts so they
# survive app restarts. Layout stored under data/dynamic_slots.json:
# {
#   "RACEKEY": {
#       "dynamic_slots": { ... },
#       "started_counts": { ... }
#   },
#   ...
# }

DYNAMIC_CACHE_PATH = cache.full_path('data', 'dynamic_slots.json')
_dynamic_cache_data = None  # lazy-loaded dict

def _load_dynamic_cache():
    global _dynamic_cache_data
    if _dynamic_cache_data is not None:
        return _dynamic_cache_data
    data = cache.read_json_if_exists(DYNAMIC_CACHE_PATH)
    if not isinstance(data, dict):
        data = {}
    _dynamic_cache_data = data
    return _dynamic_cache_data

def _save_dynamic_cache():
    if _dynamic_cache_data is None:
        return
    cache.write_json_atomic(DYNAMIC_CACHE_PATH, _dynamic_cache_data)

def persist_dynamic_state(race: dict):
    """Persist current dynamic slot and started_counts state for a race."""
    data = _load_dynamic_cache()
    key = race.get('key') or race.get('name', '').replace(' ', '_').upper()
    if not key:
        return
    entry = {}
    if 'dynamic_slots' in race:
        entry['dynamic_slots'] = race['dynamic_slots']
    if 'started_counts' in race:
        entry['started_counts'] = race['started_counts']
    if entry:
        data[key] = entry
        _save_dynamic_cache()

def hydrate_race_dynamic(race: dict):
    """Inject persisted dynamic data into race object if available.

    Intended for use during app startup after races.json load.
    """
    data = _load_dynamic_cache()
    key = race.get('key') or race.get('name', '').replace(' ', '_').upper()
    saved = data.get(key)
    if not saved:
        return
    if 'started_counts' in saved and 'started_counts' not in race:
        race['started_counts'] = saved['started_counts']
    if 'dynamic_slots' in saved and 'dynamic_slots' not in race:
        race['dynamic_slots'] = saved['dynamic_slots']

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

# Params for lightweight start count retrieval (limit result list size)
RTRT_START_COUNT_PARAMS = RTRT_LIVE_PARAMS.copy()
RTRT_START_COUNT_PARAMS['max'] = '50'

def prepare_race_urls(race: dict) -> None:
    """Populate race['results_urls']['live'] men/women URLs and parallel start URLs.

    Finish split: race['split'] if present else 'FINISH'
    Start split: race['start_split'] if present else 'START'
    Safe no-op if data incomplete.
    """
    if 'results_urls' not in race or 'live' not in race['results_urls']:
        return
    if not isinstance(race['results_urls']['live'], dict):
        return
    if 'key' not in race:
        return
    live = race['results_urls']['live']
    finish_split = race.get('split') or 'FINISH'
    start_split = race.get('start_split') or 'START'
    # Build live finish URLs
    if 'men_cat' in live:
        live['men'] = f"https://api.rtrt.me/events/{race['key']}/categories/{live['men_cat']}/splits/{finish_split}"
    if 'women_cat' in live:
        live['women'] = f"https://api.rtrt.me/events/{race['key']}/categories/{live['women_cat']}/splits/{finish_split}"
    # Build start URLs in parallel dict
    start_urls = {}
    if 'men_cat' in live:
        start_urls['men'] = f"https://api.rtrt.me/events/{race['key']}/categories/{live['men_cat']}/splits/{start_split}"
    if 'women_cat' in live:
        start_urls['women'] = f"https://api.rtrt.me/events/{race['key']}/categories/{live['women_cat']}/splits/{start_split}"
    if start_urls:
        race['results_urls']['start'] = start_urls

def fetch_start_count(api_url: str) -> int | None:
    """Fetch cattotal (number of starters) for a single start split URL.

    Returns int count or None on error.
    """
    try:
        response = requests.post(api_url, data=RTRT_START_COUNT_PARAMS)
        response.raise_for_status()
        data = response.json()
        info = data.get('info', {})
        cattotal = info.get('cattotal')
        if cattotal is None:
            return None
        return int(cattotal)
    except Exception:
        return None

def get_started_counts(race: dict) -> dict | None:
    """Retrieve or compute started counts per gender.

    Stores results in race['started_counts'] with structure:
    { men: int, women: int, computed_at: epoch_ts }

    After one hour past earliestStartTime, counts are assumed final and cached.
    """
    earliest_start = int(race.get('earliestStartTime', 0) or 0)
    if earliest_start <= 0:
        return None
    now_ts = int(datetime.now().timestamp())
    counts_existing = race.get('started_counts')
    # If already computed and race is past locking window (1h), reuse
    if counts_existing and now_ts >= earliest_start + 3600:
        return counts_existing
    start_urls = race.get('results_urls', {}).get('start', {})
    if not start_urls:
        return None
    men_url = start_urls.get('men')
    women_url = start_urls.get('women')
    men_count = fetch_start_count(men_url) if men_url else None
    women_count = fetch_start_count(women_url) if women_url else None
    # Require both counts for usefulness
    if men_count is None or women_count is None:
        return None
    counts = {
        'men': men_count,
        'women': women_count,
        'computed_at': now_ts
    }
    race['started_counts'] = counts
    # Persist counts early (will be updated again when dynamic slots computed)
    persist_dynamic_state(race)
    return counts

def compute_dynamic_slots(race: dict) -> dict | None:
    """Compute dynamic slot allocation for split-dynamic policy.

    Returns structure:
    {
      men: { winner_slots: int, pool_slots: int, total_slots: int },
      women: { winner_slots: int, pool_slots: int, total_slots: int },
      computed_at: epoch_ts
    }
    or None if prerequisites missing.
    """
    from slot_policy import resolve_slot_policy
    policy = resolve_slot_policy(race)
    if policy != 'split-dynamic':
        return None
    earliest_start = int(race.get('earliestStartTime', 0) or 0)
    now_ts = int(datetime.now().timestamp())
    if earliest_start <= 0 or now_ts < earliest_start + 3600:
        # Wait until one hour after start for stable counts
        return None
    # Total race slots must be provided (combined integer)
    try:
        total_slots = int(race.get('slots', 0))
    except Exception:
        total_slots = 0
    if total_slots <= 0:
        return None
    ag_lists = race.get('age_group_categories') or {}
    men_winner_slots = len(ag_lists.get('men', []))
    women_winner_slots = len(ag_lists.get('women', []))
    if men_winner_slots == 0 and women_winner_slots == 0:
        return None
    counts = get_started_counts(race)
    if not counts:
        return None
    men_started = counts.get('men', 0)
    women_started = counts.get('women', 0)
    total_started = men_started + women_started
    if total_started <= 0:
        return None
    auto_slots_total = men_winner_slots + women_winner_slots
    performance_pool = total_slots - auto_slots_total
    if performance_pool < 0:
        performance_pool = 0
    men_ratio = men_started / total_started
    women_ratio = women_started / total_started
    # Initial rounding
    men_pool = round(performance_pool * men_ratio)
    women_pool = performance_pool - men_pool  # ensure sum matches
    result = {
        'men': {
            'winner_slots': men_winner_slots,
            'pool_slots': men_pool,
            'total_slots': men_winner_slots + men_pool
        },
        'women': {
            'winner_slots': women_winner_slots,
            'pool_slots': women_pool,
            'total_slots': women_winner_slots + women_pool
        },
        'computed_at': now_ts
    }
    race['dynamic_slots'] = result
    persist_dynamic_state(race)
    return result

def annotate_slot_allocation(results_list, race_obj, selected_gender):
    """Annotate result entries with slot allocation flags (ag_winner, pool_qualifier).

    Supports fixed policies and dynamic split allocation.
    """
    from slot_policy import resolve_slot_policy
    from slot_policy import policy_needs_gender
    policy_local = resolve_slot_policy(race_obj)

    # Determine total slots for this context
    total_slots = 0
    if policy_local == 'split-fixed':
        slots_info = race_obj.get('slots', {})
        if isinstance(slots_info, dict) and selected_gender in slots_info:
            total_slots = int(slots_info.get(selected_gender, 0))
    elif policy_local == 'combined-fixed':
        try:
            total_slots = int(race_obj.get('slots', 0))
        except (TypeError, ValueError):
            total_slots = 0
    elif policy_local == 'split-dynamic':
        dynamic = race_obj.get('dynamic_slots') or compute_dynamic_slots(race_obj)
        if dynamic and selected_gender in dynamic:
            total_slots = int(dynamic[selected_gender]['total_slots'])
        else:
            # If dynamic not ready yet, skip annotation
            return results_list

    # Initialize flags
    for a in results_list:
        a['ag_winner'] = False
        a['pool_qualifier'] = False

    if total_slots <= 0 or not results_list:
        return results_list

    # Identify age group winners (AG place == 1)
    winners_by_ag = set()
    for a in results_list:
        if a.get('ag_place') == 1:
            a['ag_winner'] = True
            winners_by_ag.add(a.get('age_group'))

    remaining = max(0, total_slots - len(winners_by_ag))
    if remaining == 0:
        return results_list

    for a in results_list:
        if remaining <= 0:
            break
        if not a.get('ag_winner'):
            a['pool_qualifier'] = True
            remaining -= 1
    return results_list

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

from slot_policy import resolve_slot_policy, policy_needs_gender


def get_processed_results(race, gender, ag_adjustments):
    """
    Unified function to fetch and process results based on race distance and gender.
    """
    all_data_list = []
    policy = resolve_slot_policy(race)
    distance = race.get('distance')

    if policy_needs_gender(policy):
        # Expect a gender-specific URL
        if not gender:
            gender = 'men'
        api_url = race['results_urls']['live'].get(gender)
        raw_data = fetch_live_results(api_url)
        if "error" in raw_data:
            return raw_data
        all_data_list.extend(raw_data.get("list", []))
    else:
        # Combined pool: merge men and women
        if distance not in ('70.3', '140.6'):
            return {"error": f"Invalid race distance: {distance}"}
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

    if not all_data_list:
        return {"error": "No live data found for the selected race."}

    return process_live_results(all_data_list, ag_adjustments)


def get_processed_results_cached(race, gender, ag_adjustments):
    """
    Fetch and process results with caching semantics based on race time:
    - Before 24 hours after earliestStartTime: use IN_PROGRESS cache only
        * If in_progress cache is fresh: return it
        * Otherwise: fetch, process, store to in_progress, return
    - At or after configured delay (FINAL_CACHE_DELAY_HOURS, default 24h) post earliestStartTime: use FINAL cache
        * If final cache exists: return it
        * Otherwise: fetch, process, store to final, return

    Freshness window for in_progress is configurable via app config 'CACHE_FRESHNESS_SECONDS' (default 60).
    """
    freshness = int(current_app.config.get('CACHE_FRESHNESS_SECONDS', 60))

    distance = race.get('distance')
    policy = resolve_slot_policy(race)
    # Normalize gender for split policies; ignored for combined-fixed
    effective_gender = None
    if policy_needs_gender(policy):
        effective_gender = gender or 'men'

    # Determine cache paths
    final_path = cache.get_cache_file_path(race, 'final', effective_gender)
    inprog_path = cache.get_cache_file_path(race, 'in_progress', effective_gender)

    # Determine if FINAL cache should be used based on time since race start
    earliest_start = int(race.get('earliestStartTime', 0) or 0)
    now_ts = int(datetime.now().timestamp())
    delay_hours = int(current_app.config.get('FINAL_CACHE_DELAY_HOURS', 24))
    final_ready = earliest_start > 0 and now_ts >= (earliest_start + delay_hours * 3600)

    if final_ready:
        # Prefer existing FINAL cache
        data = cache.read_json_if_exists(final_path)
        if data is not None:
            current_app.logger.debug(f"Serving results from FINAL cache: {final_path}")
            return data

        # Build FINAL cache from live
        current_app.logger.info(f"FINAL window reached; fetching live to build FINAL for {race.get('key')}")
        data = get_processed_results(race, effective_gender, ag_adjustments)
        if isinstance(data, dict) and 'error' in data:
            return data
        cache.write_json(final_path, data)
        return data

    # Pre-final window: try IN_PROGRESS cache first
    if cache.is_fresh(inprog_path, freshness):
        data = cache.read_json_if_exists(inprog_path)
        if data is not None:
            current_app.logger.debug(f"Serving results from IN_PROGRESS cache: {inprog_path}")
            return data

    # Not fresh or not present; fetch and update IN_PROGRESS
    current_app.logger.debug(f"Fetching live results for {race.get('key')} (updating IN_PROGRESS cache)")
    data = get_processed_results(race, effective_gender, ag_adjustments)
    if not (isinstance(data, dict) and 'error' in data):
        cache.write_json(inprog_path, data)
    return data