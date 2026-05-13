import os
import json
import math
import time
from flask import Flask, render_template, abort, jsonify, redirect, url_for, current_app, request
from datetime import date, datetime, timedelta
import parse_live_data
import adjustments
from slot_policy import resolve_slot_policy, policy_needs_gender

app = Flask(__name__)
app.config['CACHE_FRESHNESS_SECONDS'] = int(os.getenv('CACHE_FRESHNESS_SECONDS', '60'))
app.config['FINAL_CACHE_DELAY_HOURS'] = int(os.getenv('FINAL_CACHE_DELAY_HOURS', '24'))

def full_path(relative_path):
    return os.path.join(os.path.dirname(__file__), relative_path)

# Function to load AG adjustments from a JSON file
def load_ag_adjustments(file_path):
    try:
        with open(full_path(file_path), 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        current_app.logger.error(f"Age-graded adjustments file not found at '{file_path}'")
        raise
    except json.JSONDecodeError:
        current_app.logger.error(f"Invalid JSON format in '{file_path}'")
        raise

# Deprecated: Static load of adjustments. Left for backward compat if needed.
# AG_ADJUSTMENTS_703 = load_ag_adjustments('ag_adjustments_703.json')
# AG_ADJUSTMENTS_1406 = load_ag_adjustments('ag_adjustments_1406.json')

# Global variables to track file modification times and last check time
ALL_RACES_LAST_MODIFIED = 0
LAST_FILE_CHECK_TIME = 0

# Functions to convert between display names and URL-friendly names
def to_url_friendly_name(race_name):
    return race_name.replace(' ', '_')

def from_url_friendly_name(url_name):
    return url_name.replace('_', ' ')

def choose_default_gender(race: dict) -> str:
    """Choose a default gender when a policy requires it but one URL may be missing.

    Prefers 'men' if available; falls back to 'women' if men's live URL is missing.
    """
    live = race.get('results_urls', {}).get('live', {}) or {}
    if isinstance(live, dict) and live.get('men'):
        return 'men'
    if isinstance(live, dict) and live.get('women'):
        return 'women'
    return 'men'

# Function to load and process all races at startup
def load_and_process_races():
    global ALL_RACES_LAST_MODIFIED

    races_file_path = full_path('races.json')

    # Get the current modification time of races.json
    try:
        file_mod_time = os.path.getmtime(races_file_path)
        ALL_RACES_LAST_MODIFIED = file_mod_time
    except OSError:
        # If we can't get the modification time, set it to current time
        ALL_RACES_LAST_MODIFIED = time.time()

    with open(races_file_path, 'r', encoding='utf-8') as f:
        races = json.load(f)

    # Process URLs for all races
    for race in races:
        # Add URL for race page using to_url_friendly_name
        race['url'] = f"/results/{to_url_friendly_name(race['name'])}/"
        # Build live & start URLs (function lives in parse_live_data; already imported at top)
        try:
            parse_live_data.prepare_race_urls(race)
        except Exception:
            # Non-critical; continue loading other races
            pass

        # Hydrate persisted dynamic slot / started counts state if present
        try:
            parse_live_data.hydrate_race_dynamic(race)
        except Exception:
            # Safe to ignore; persistence is optional
            pass

        # Annotate slot policy for front-end convenience (already imported at top)
        try:
            race['slot_policy'] = resolve_slot_policy(race)
        except Exception:
            # Do not fail if policy cannot be resolved; front-end will fall back to distance
            pass

    # Sort by earliestStartTime in descending order (once at startup)
    races.sort(key=lambda x: int(x.get('earliestStartTime', 0)), reverse=True)

    return races

# Load and process all races at app startup
ALL_RACES = load_and_process_races()

# Function to filter races based on cutoff timestamp
def filter_races_by_timestamp(races, debug_mode=False):
    # Get cutoff timestamp
    cutoff = int(datetime.now().timestamp())
    if debug_mode:
        cutoff += 7 * 24 * 60 * 60  # Add 7 days in debug mode
    else:
        # Include nearest upcoming weekend races in non-debug mode.
        cutoff += 6 * 24 * 60 * 60

    # Filter races based on cutoff
    filtered_races = []
    for race in races:
        if 'earliestStartTime' in race and int(race['earliestStartTime']) <= cutoff:
            filtered_races.append(race)

    return filtered_races

# Helper function to check if races.json needs to be reloaded
def should_reload_races():
    """
    Check if races.json has been modified since we last loaded it.
    Includes rate limiting to avoid checking the filesystem too frequently.
    """
    global LAST_FILE_CHECK_TIME

    current_time = time.time()

    # Only check the filesystem once per minute maximum
    next_check_time = LAST_FILE_CHECK_TIME + 60
    if current_time < next_check_time:
        current_app.logger.debug("Skipping races.json check to avoid frequent filesystem access.  Next check time at %s (currently %s)", time.ctime(next_check_time), time.ctime(current_time))
        return False

    LAST_FILE_CHECK_TIME = current_time

    try:
        races_file_path = full_path('races.json')
        current_file_mod_time = os.path.getmtime(races_file_path)
        should_reload = current_file_mod_time > ALL_RACES_LAST_MODIFIED

        current_app.logger.debug("Races.json modification check: last loaded at %s, current mod time %s. %s", time.ctime(ALL_RACES_LAST_MODIFIED), time.ctime(current_file_mod_time), "Reloading." if should_reload else "Using cached data.")
        # If the file has been modified since we last loaded it, we should reload
        return should_reload
    except OSError:
        # If we can't check the file, don't reload (use cached data)
        return False

# Function to get filtered race data (already sorted at startup)
def get_races():
    global ALL_RACES

    # Check if we need to reload the races data
    if should_reload_races():
        try:
            current_app.logger.info("Races.json has been modified, reloading data...")
            ALL_RACES = load_and_process_races()
        except Exception as e:
            current_app.logger.error(f"Error reloading races.json: {e}")
            # Continue with cached data if reload fails

    # Filter the pre-loaded races data
    filtered_races = filter_races_by_timestamp(ALL_RACES, current_app.debug)

    return filtered_races

# A reusable function to get race data by name
def get_race_by_name(race_name):
    races = get_races()
    display_name = from_url_friendly_name(race_name)
    race_data = next((r for r in races if r['name'] == display_name), None)
    return race_data

# Function to get rolldown information for display
def get_rolldown_info(race, gender=None):
    """
    Get rolldown information for a race.
    Returns dict with 'has_data', 'position', 'gender_text', and 'message_type'
    """
    known_rolldown = race.get('known_rolldown')

    if not known_rolldown:
        return {
            'has_data': False,
            'message_type': 'no_data',
            'position': None,
            'gender_text': ''
        }

    if race['distance'] == '70.3':
        if gender and gender in known_rolldown:
            position = known_rolldown[gender]
            if isinstance(position, int):
                gender_text = 'men' if gender == 'men' else 'women'
                return {
                    'has_data': True,
                    'message_type': 'has_data',
                    'position': position,
                    'gender_text': gender_text
                }
        return {
            'has_data': False,
            'message_type': 'no_data',
            'position': None,
            'gender_text': gender if gender else ''
        }

    elif race['distance'] == '140.6':
        if isinstance(known_rolldown, int):
            return {
                'has_data': True,
                'message_type': 'has_data',
                'position': known_rolldown,
                'gender_text': ''
            }
        return {
            'has_data': False,
            'message_type': 'no_data',
            'position': None,
            'gender_text': ''
        }

    return {
        'has_data': False,
        'message_type': 'no_data',
        'position': None,
        'gender_text': ''
    }


VALID_SOURCES = {'live', 'official_ag'}
VALID_GENDERS = {'men', 'women'}


def race_has_official_ag(race: dict) -> bool:
    official = race.get('results_urls', {}).get('official_ag')
    if race.get('distance') == '70.3':
        if not isinstance(official, dict):
            return False
        men = official.get('men') if isinstance(official.get('men'), str) else ''
        women = official.get('women') if isinstance(official.get('women'), str) else ''
        return bool((men and men.strip()) or (women and women.strip()))

    if isinstance(official, str):
        return bool(official.strip())
    if isinstance(official, dict):
        men = official.get('men') if isinstance(official.get('men'), str) else ''
        women = official.get('women') if isinstance(official.get('women'), str) else ''
        return bool((men and men.strip()) or (women and women.strip()))
    return False


def official_ag_is_gendered(race: dict) -> bool:
    if race.get('distance') != '140.6':
        return False
    official = race.get('results_urls', {}).get('official_ag')
    if not isinstance(official, dict):
        return False
    men = official.get('men') if isinstance(official.get('men'), str) else ''
    women = official.get('women') if isinstance(official.get('women'), str) else ''
    return bool((men and men.strip()) or (women and women.strip()))


def default_source_for_race(race: dict) -> str:
    has_official_ag = race_has_official_ag(race)
    earliest_start = int(race.get('earliestStartTime', 0) or 0)
    now_ts = int(datetime.now().timestamp())

    if earliest_start > 0:
        if now_ts < earliest_start:
            return 'live'
        window_hours = 16 if race.get('distance') == '140.6' else 8
        if now_ts < (earliest_start + window_hours * 3600):
            return 'live'

    return 'official_ag' if has_official_ag else 'live'


def normalize_selection(race: dict, source: str | None = None, gender: str | None = None):
    next_source = source if source in VALID_SOURCES else default_source_for_race(race)
    has_official_ag = race_has_official_ag(race)
    if next_source == 'official_ag' and not has_official_ag:
        next_source = 'live'

    policy = resolve_slot_policy(race)
    requires_gender = policy_needs_gender(policy) or (next_source == 'official_ag' and official_ag_is_gendered(race))
    if requires_gender:
        if gender not in VALID_GENDERS:
            gender = choose_default_gender(race)
    else:
        gender = None

    return {
        'race': race,
        'source': next_source,
        'gender': gender,
        'requires_gender': requires_gender,
    }


def build_iframe_state(race: dict, source: str, gender: str | None = None):
    iframe_url = None
    coming_soon = True

    if source == 'official_ag':
        official = race.get('results_urls', {}).get('official_ag')
        if race.get('distance') == '70.3' and isinstance(official, dict) and gender:
            iframe_url = official.get(gender) or ''
        elif race.get('distance') == '140.6':
            if isinstance(official, dict) and gender:
                iframe_url = official.get(gender) or ''
            elif isinstance(official, str):
                iframe_url = official
        coming_soon = bool(iframe_url)
    else:
        coming_soon = False

    return {
        'iframe_url': iframe_url,
        'coming_soon': coming_soon,
    }


def build_canonical_url(race_name: str, source: str, gender: str | None = None) -> str:
    params = [('race', race_name), ('source', source)]
    if gender:
        params.append(('gender', gender))
    query = '&'.join(f"{k}={v}" for k, v in params)
    return f"/?{query}"


def parse_race_date(race: dict) -> date | None:
    raw = race.get('date')
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except Exception:
        return None


def _nearest_date_for_weekday(today: date, target_weekday: int) -> date:
    days_since = (today.weekday() - target_weekday) % 7
    previous = today - timedelta(days=days_since)
    next_date = previous + timedelta(days=7)
    if abs((today - previous).days) <= abs((next_date - today).days):
        return previous
    return next_date


def nearest_weekend_dates(today: date) -> tuple[date, date]:
    nearest_saturday = _nearest_date_for_weekday(today, 5)
    nearest_sunday = _nearest_date_for_weekday(today, 6)
    if abs((nearest_saturday - today).days) <= abs((nearest_sunday - today).days):
        saturday = nearest_saturday
        sunday = saturday + timedelta(days=1)
    else:
        sunday = nearest_sunday
        saturday = sunday - timedelta(days=1)
    return saturday, sunday


def partition_races_for_controls(races: list[dict], today: date | None = None):
    today = today or datetime.now().date()
    saturday, sunday = nearest_weekend_dates(today)
    weekend_dates = {saturday, sunday}

    current = []
    archive = []
    for race in races:
        race_date = parse_race_date(race)
        if race_date and race_date in weekend_dates:
            current.append(race)
        else:
            archive.append(race)

    return current, archive


def pick_default_race(races: list[dict], today: date | None = None):
    if not races:
        return None
    today = today or datetime.now().date()

    def distance_key(race: dict):
        race_date = parse_race_date(race)
        if not race_date:
            return (10_000, 1)
        diff = abs((race_date - today).days)
        # Prefer future when equally close so users land on currently relevant weekends.
        bias = 0 if race_date >= today else 1
        return (diff, bias)

    return min(races, key=distance_key)


def format_current_race_label(name: str) -> str:
    if not isinstance(name, str):
        return ""
    label = name.replace("Ironman 70.3 ", "IM70.3 ")
    label = label.replace("Ironman ", "IM ")
    return label

@app.route('/')
def home():
    races = get_races()
    default_race = pick_default_race(races)

    if not default_race:
        return render_template('index.html',
                             page_title='Long-Course Age Graded Results',
                             races=[],
                             selected_race='')

    race_name = request.args.get('race') or to_url_friendly_name(default_race['name'])
    selected_race = get_race_by_name(race_name) or default_race
    selected_source_arg = request.args.get('source')
    selected_gender_arg = request.args.get('gender')

    normalized = normalize_selection(selected_race, selected_source_arg, selected_gender_arg)
    source = normalized['source']
    gender = normalized['gender']

    iframe_state = build_iframe_state(selected_race, source, gender)
    slot_summary = compute_slot_summary(selected_race, gender)
    current_races, archive_races = partition_races_for_controls(races)
    current_races = [
        {**race, "control_label": format_current_race_label(race.get("name", ""))}
        for race in current_races
    ]

    return render_template(
        'index.html',
        page_title='Long-Course Age Graded Results',
        races=races,
        current_races=current_races,
        archive_races=archive_races,
        selected_race=selected_race['name'],
        selected_source=source,
        selected_gender=gender,
        iframe_url=iframe_state['iframe_url'],
        coming_soon=iframe_state['coming_soon'],
        slot_summary=slot_summary
    )

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/rolldowns')
def rolldowns():
    races = get_races()

    # Separate races by distance
    races_703 = [race for race in races if race['distance'] == '70.3']
    races_1406 = [race for race in races if race['distance'] == '140.6']

    # Calculate averages for 70.3 races
    men_703_rolldowns = []
    women_703_rolldowns = []

    for race in races_703:
        rolldown = race.get('known_rolldown', {})
        if isinstance(rolldown, dict):
            if rolldown.get('men') is not None:
                men_703_rolldowns.append(rolldown['men'])
            if rolldown.get('women') is not None:
                women_703_rolldowns.append(rolldown['women'])

    men_703_average = math.floor(sum(men_703_rolldowns) / len(men_703_rolldowns)) if men_703_rolldowns else None
    women_703_average = math.floor(sum(women_703_rolldowns) / len(women_703_rolldowns)) if women_703_rolldowns else None

    # Calculate averages for 140.6 races
    rolldowns_1406 = []

    for race in races_1406:
        rolldown = race.get('known_rolldown')
        if isinstance(rolldown, int):
            rolldowns_1406.append(rolldown)

    average_1406 = math.floor(sum(rolldowns_1406) / len(rolldowns_1406)) if rolldowns_1406 else None

    return render_template('rolldowns.html',
                         races_703=races_703,
                         races_1406=races_1406,
                         men_703_average=men_703_average,
                         women_703_average=women_703_average,
                         average_1406=average_1406)

@app.route('/results/<race_name>')
@app.route('/results/<race_name>/')
def redirect_to_results(race_name):
    race = get_race_by_name(race_name)

    if not race:
        abort(404)
    normalized = normalize_selection(race)
    canonical = build_canonical_url(race_name, normalized['source'], normalized['gender'])
    return redirect(canonical, code=302)

@app.route('/results/<race_name>/<data_source>')
@app.route('/results/<race_name>/<data_source>/<gender>')
def display_results(race_name, data_source, gender=None):
    race = get_race_by_name(race_name)

    if not race:
        abort(404)

    normalized = normalize_selection(race, data_source, gender)
    canonical = build_canonical_url(race_name, normalized['source'], normalized['gender'])
    return redirect(canonical, code=302)

def get_race_status_message(race):
    """
    Determine the current race status and appropriate message based on timing.
    Returns a tuple of (message_dict, should_fetch_results).
    """
    current_time = int(datetime.now().timestamp())
    earliest_start = int(race.get('earliestStartTime', 0))

    if current_time < earliest_start:
        return {
            'text': "This race hasn't yet started. Racers should be on the course starting around:",
            'timestamp': earliest_start * 1000  # Convert to milliseconds for JavaScript
        }, False

    finish_offset = timedelta(hours=7.5 if race['distance'] == '140.6' else 3.5)
    expected_finish = datetime.fromtimestamp(earliest_start) + finish_offset

    if current_time < expected_finish.timestamp():
        return {
            'text': "Racers are probably on the course right now. Results will start filling in here as they cross the finish line, likely sometime after:",
            'timestamp': int(expected_finish.timestamp() * 1000)  # Convert to milliseconds for JavaScript
        }, False

    return None, True

def compute_slot_summary(race, selected_gender=None):
    """Build a slot allocation summary for the UI.

    Returns a dict with keys describing either combined or per-gender allocation.
    For dynamic split races, includes a waiting flag until pool allocation is available.
    """
    def ag_count(g):
        return len((race.get('age_group_categories') or {}).get(g, []))

    policy = resolve_slot_policy(race)
    summary = { 'policy': policy }

    try:
        if policy == 'combined-fixed':
            total = int(race.get('slots', 0) or 0)
            men_c = set((race.get('age_group_categories') or {}).get('men', []))
            women_c = set((race.get('age_group_categories') or {}).get('women', []))
            winners = len(men_c.union(women_c))
            pool = max(0, total - winners)
            incomplete = (winners == 0)
            summary.update({
                'mode': 'combined',
                'total_slots': total,
                'winner_slots': winners,
                'pool_slots': pool,
                'incomplete': incomplete
            })
            return summary

        if policy == 'split-fixed':
            slots_map = race.get('slots') or {}
            men_total = int(slots_map.get('men', 0) or 0)
            women_total = int(slots_map.get('women', 0) or 0)
            men_w = ag_count('men')
            women_w = ag_count('women')
            men_pool = max(0, men_total - men_w)
            women_pool = max(0, women_total - women_w)
            incomplete = (men_w == 0 or women_w == 0)
            summary.update({
                'mode': 'split',
                'per_gender': {
                    'men': {
                        'total_slots': men_total,
                        'winner_slots': men_w,
                        'pool_slots': men_pool
                    },
                    'women': {
                        'total_slots': women_total,
                        'winner_slots': women_w,
                        'pool_slots': women_pool
                    }
                },
                'combined': {
                    'total_slots': men_total + women_total,
                    'winner_slots': men_w + women_w,
                    'pool_slots': men_pool + women_pool
                },
                'waiting': False,
                'incomplete': incomplete
            })
            return summary

        if policy == 'split-dynamic':
            dynamic = race.get('dynamic_slots')
            # Minimal enhancement: if dynamic allocation not yet computed but
            # we may be past the 1h stabilization window, attempt computation
            # here so the slot summary can flip from Waiting to Ready even
            # before any finishers are processed.
            if not dynamic:
                try:
                    dynamic = parse_live_data.compute_dynamic_slots(race)
                    if dynamic:
                        current_app.logger.info("On-demand dynamic slot allocation computed for race %s", race.get('key') or race.get('name'))
                    else:
                        current_app.logger.debug("On-demand dynamic slot allocation not ready yet for race %s", race.get('key') or race.get('name'))
                except Exception as e:
                    current_app.logger.warning("On-demand dynamic slot allocation failed for race %s: %s", race.get('key') or race.get('name'), e)
                    dynamic = None
            men_w = ag_count('men')
            women_w = ag_count('women')
            combined_total = 0
            try:
                combined_total = int(race.get('slots', 0) or 0)
            except Exception:
                combined_total = 0
            incomplete = (men_w == 0 or women_w == 0)

            if dynamic and isinstance(dynamic, dict) and 'men' in dynamic and 'women' in dynamic:
                # Ready state with per-gender totals
                summary.update({
                    'mode': 'split',
                    'per_gender': {
                        'men': {
                            'total_slots': int(dynamic['men'].get('total_slots', 0)),
                            'winner_slots': int(dynamic['men'].get('winner_slots', 0)),
                            'pool_slots': int(dynamic['men'].get('pool_slots', 0))
                        },
                        'women': {
                            'total_slots': int(dynamic['women'].get('total_slots', 0)),
                            'winner_slots': int(dynamic['women'].get('winner_slots', 0)),
                            'pool_slots': int(dynamic['women'].get('pool_slots', 0))
                        }
                    },
                    'combined': {
                        'total_slots': combined_total,
                        'winner_slots': men_w + women_w,
                        'pool_slots': max(0, combined_total - (men_w + women_w))
                    },
                    'waiting': False,
                    'incomplete': incomplete
                })
                return summary
            else:
                # Not ready yet: show combined totals and winners; leave pool per-gender pending
                summary.update({
                    'mode': 'split',
                    'per_gender': {
                        'men': {
                            'total_slots': None,
                            'winner_slots': men_w,
                            'pool_slots': None
                        },
                        'women': {
                            'total_slots': None,
                            'winner_slots': women_w,
                            'pool_slots': None
                        }
                    },
                    'combined': {
                        'total_slots': combined_total,
                        'winner_slots': men_w + women_w,
                        'pool_slots': max(0, combined_total - (men_w + women_w))
                    },
                    'waiting': True,
                    'incomplete': incomplete
                })
                return summary
    except Exception as e:
        current_app.logger.debug(f"Slot summary build failed: {e}")

    return { 'policy': policy, 'mode': 'unknown' }

@app.route('/live_results/<race_name>')
@app.route('/live_results/<race_name>/<gender>')
def live_results_table(race_name, gender=None):
    race = get_race_by_name(race_name)
    if not race:
        return jsonify({"error": "Race not found"}), 404

    selected_theme = request.args.get('theme')
    if selected_theme not in ('light', 'dark'):
        selected_theme = None

    # Determine gender and adjustments based on slot policy
    policy = resolve_slot_policy(race)
    if policy_needs_gender(policy):
        if not gender:
            gender = choose_default_gender(race)
        if gender not in race['results_urls']['live']:
            return jsonify({"error": f"Live results for {race['distance']} {gender} not supported"}), 404
    else:
        # Combined view: require both URLs to merge
        if 'men' not in race['results_urls']['live'] or 'women' not in race['results_urls']['live']:
            return jsonify({"error": "Live results URLs for both men and women must be provided for combined view"}), 404

    # Select adjustments factors based on manifest and per-race lock
    try:
        ag_adjustments, adjustments_version = adjustments.get_adjustments_for_race(race)
        # annotate for templates/debug if needed
        race['adjustments_version'] = adjustments_version
    except Exception as e:
        current_app.logger.error(f"Failed to resolve adjustments for race {race.get('key')}: {e}")
        return jsonify({"error": "Unable to load adjustments for this race"}), 500

    # Build slot summary for UI
    slot_summary = compute_slot_summary(race, gender)

    # Check if we should fetch results based on race timing
    message, should_fetch_results = get_race_status_message(race)
    if not should_fetch_results:
        return render_template('live_results.html', results=[], error=message, slot_summary=slot_summary, selected_theme=selected_theme)

    # Use caching-aware retrieval to reduce load on RTRT servers
    processed_data = parse_live_data.get_processed_results_cached(race, gender, ag_adjustments)

    if "error" in processed_data:
        # Handle error cases
        if isinstance(processed_data["error"], str) and processed_data["error"] == "no_finishers":
            message = {
                'text': "No racers have crossed the finish line yet. Results will appear here as soon as racers finish.",
                'timestamp': None
            }
            return render_template('live_results.html', results=[], error=message, selected_theme=selected_theme)
        else:
            return render_template('live_results.html', results=[], error=processed_data["error"], selected_theme=selected_theme)

    # Annotate slot allocation including dynamic logic
    try:
        processed_data = parse_live_data.annotate_slot_allocation(processed_data, race, gender)
    except Exception as e:
        current_app.logger.warning(f"Slot allocation annotation failed: {e}")

    return render_template('live_results.html', results=processed_data, slot_summary=slot_summary, selected_theme=selected_theme)

@app.route('/fragment/slot_summary/<race_name>')
def fragment_slot_summary(race_name):
    """Return the rendered slot summary partial for a given race (and optional gender).

    Usage: /fragment/slot_summary/<race_name>?gender=men|women
    """
    race = get_race_by_name(race_name)
    if not race:
        abort(404)

    gender = request.args.get('gender') or None
    try:
        slot_summary = compute_slot_summary(race, gender)
    except Exception:
        slot_summary = None

    # Return only the partial HTML (no layout)
    return render_template('partials/slot_summary.html', slot_summary=slot_summary)

@app.route('/reset')
def reset():
    """Reset route that forces the app to reread races.json and update caches."""
    global ALL_RACES

    try:
        # Reload and reprocess all races from disk
        ALL_RACES = load_and_process_races()
        current_app.logger.info("Successfully reloaded races.json and updated caches")
    except Exception as e:
        current_app.logger.error(f"Error reloading races.json: {e}")
        # Even if there's an error, redirect to home to show current state

    # Redirect to the root route
    return redirect(url_for('home'))

if __name__ == '__main__':
    debug_mode = 'PYTHONANYWHERE_SITE' not in os.environ
    app.run(debug=debug_mode)
