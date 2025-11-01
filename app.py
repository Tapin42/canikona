import os
import json
import math
import time
from flask import Flask, render_template, abort, jsonify, redirect, url_for, current_app
from datetime import date, datetime, timedelta
import parse_live_data
import adjustments

app = Flask(__name__)
app.config['CACHE_FRESHNESS_SECONDS'] = int(os.getenv('CACHE_FRESHNESS_SECONDS', '60'))

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

        if ('results_urls' in race and 'live' in race['results_urls'] and
            isinstance(race['results_urls']['live'], dict) and 'key' in race):
            live = race['results_urls']['live']
            split = race['split'] if 'split' in race else 'FINISH'

            # Process men's URL
            if 'men_cat' in live:
                men_url = f"https://api.rtrt.me/events/{race['key']}/categories/{live['men_cat']}/splits/{split}"
                live['men'] = men_url

            # Process women's URL
            if 'women_cat' in live:
                women_url = f"https://api.rtrt.me/events/{race['key']}/categories/{live['women_cat']}/splits/{split}"
                live['women'] = women_url

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
        cutoff += 1 * 24 * 60 * 60  # Add 1 day otherwise

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

@app.route('/')
def home():
    races = get_races()
    default_race = races[0] if races else None

    if default_race:
        # Redirect to the default race using the new results route
        race_name = to_url_friendly_name(default_race['name'])
        return redirect(url_for('redirect_to_results', race_name=race_name))
    else:
        # No races available, show empty page or error
        return render_template('index.html',
                             page_title='Long-Course Age Graded Results',
                             races=[],
                             selected_race='')

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

    # Determine availability of official_ag results
    has_official_ag = False
    if 'results_urls' in race and 'official_ag' in race['results_urls']:
        if race['distance'] == '70.3':
            has_official_ag = bool(race['results_urls']['official_ag'].get('men'))
        else:  # 140.6
            has_official_ag = bool(race['results_urls']['official_ag'])

    # Default to Live for future races and within initial post-start windows:
    # - Future (before earliestStartTime): Live
    # - 140.6: first 16 hours after earliestStartTime: Live
    # - 70.3: first 8 hours after earliestStartTime: Live
    earliest_start = int(race.get('earliestStartTime', 0) or 0)
    now_ts = int(datetime.now().timestamp())
    within_window = False
    if earliest_start > 0:
        if now_ts < earliest_start:
            # Race hasn't started yet; default to Live
            if race['distance'] == '140.6':
                return redirect(url_for('display_results', race_name=race_name, data_source='live'))
            else:  # 70.3
                return redirect(url_for('display_results', race_name=race_name, data_source='live', gender='men'))
        else:
            window_hours = 16 if race['distance'] == '140.6' else 8
            within_window = now_ts < (earliest_start + window_hours * 3600)

    # Decide data_source with new default rules
    if within_window:
        if race['distance'] == '140.6':
            return redirect(url_for('display_results', race_name=race_name, data_source='live'))
        else:  # 70.3
            return redirect(url_for('display_results', race_name=race_name, data_source='live', gender='men'))

    # Outside the window: prefer official if available, else live
    if race['distance'] == '140.6':
        return redirect(url_for('display_results', race_name=race_name, data_source='official_ag' if has_official_ag else 'live'))
    else:  # 70.3
        return redirect(url_for('display_results', race_name=race_name, data_source='official_ag' if has_official_ag else 'live', gender='men'))

@app.route('/results/<race_name>/<data_source>')
@app.route('/results/<race_name>/<data_source>/<gender>')
def display_results(race_name, data_source, gender=None):
    races = get_races()
    race = get_race_by_name(race_name)

    if not race:
        abort(404)

    iframe_url = None
    coming_soon = True

    if data_source == 'official_ag':
        if 'official_ag' in race.get('results_urls', {}):
            if race['distance'] == '70.3' and gender:
                iframe_url = race['results_urls']['official_ag'].get(gender)
            elif race['distance'] == '140.6':
                iframe_url = race['results_urls']['official_ag']
            coming_soon = iframe_url != ""

    return render_template(
        'index.html',
        page_title='Long-Course Age Graded Results',
        races=races,
        selected_race=from_url_friendly_name(race_name),
        selected_source=data_source,
        selected_gender=gender,
        iframe_url=iframe_url,
        coming_soon=coming_soon
    )

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

@app.route('/live_results/<race_name>')
@app.route('/live_results/<race_name>/<gender>')
def live_results_table(race_name, gender=None):
    race = get_race_by_name(race_name)
    if not race:
        return jsonify({"error": "Race not found"}), 404

    # Determine gender and adjustments
    if race['distance'] == '70.3':
        if not gender:
            gender = 'men'  # Default to men if not provided
        if gender not in race['results_urls']['live']:
            return jsonify({"error": f"Live results for {race['distance']} {gender} not supported"}), 404
    elif race['distance'] == '140.6':
        if 'men' not in race['results_urls']['live'] or 'women' not in race['results_urls']['live']:
            return jsonify({"error": "Live results URLs for both men and women must be provided for 140.6 races"}), 404
    else:
        return jsonify({"error": f"Invalid race distance: {race['distance']}"}), 400

    # Select adjustments factors based on manifest and per-race lock
    try:
        ag_adjustments, adjustments_version = adjustments.get_adjustments_for_race(race)
        # annotate for templates/debug if needed
        race['adjustments_version'] = adjustments_version
    except Exception as e:
        current_app.logger.error(f"Failed to resolve adjustments for race {race.get('key')}: {e}")
        return jsonify({"error": "Unable to load adjustments for this race"}), 500

    # Check if we should fetch results based on race timing
    message, should_fetch_results = get_race_status_message(race)
    if not should_fetch_results:
        return render_template('live_results.html', results=[], error=message)

    # Use caching-aware retrieval to reduce load on RTRT servers
    processed_data = parse_live_data.get_processed_results_cached(race, gender, ag_adjustments)

    if "error" in processed_data:
        # Handle error cases
        if isinstance(processed_data["error"], str) and processed_data["error"] == "no_finishers":
            message = {
                'text': "No racers have crossed the finish line yet. Results will appear here as soon as racers finish.",
                'timestamp': None
            }
            return render_template('live_results.html', results=[], error=message)
        else:
            return render_template('live_results.html', results=[], error=processed_data["error"])

    # Compute and annotate automatic slot allocation highlights (no rolldown assumption)
    def annotate_slot_allocation(results_list, race_obj, selected_gender):
        try:
            # Determine total slots for this context
            total_slots = 0
            if race_obj.get('distance') == '70.3':
                # Slots are gendered for 70.3
                slots_info = race_obj.get('slots', {})
                if isinstance(slots_info, dict) and selected_gender in slots_info:
                    total_slots = int(slots_info.get(selected_gender, 0))
            elif race_obj.get('distance') == '140.6':
                # Single pool of slots across all age groups and genders
                try:
                    total_slots = int(race_obj.get('slots', 0))
                except (TypeError, ValueError):
                    total_slots = 0

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

            # Remaining slots after giving one to each AG winner
            remaining = max(0, total_slots - len(winners_by_ag))

            if remaining == 0:
                return results_list

            # Allocate remaining slots from top of graded list excluding AG winners
            for a in results_list:
                if remaining <= 0:
                    break
                if not a.get('ag_winner'):
                    a['pool_qualifier'] = True
                    remaining -= 1

            return results_list
        except Exception as e:
            current_app.logger.warning(f"Error annotating slot allocation: {e}")
            return results_list

    processed_data = annotate_slot_allocation(processed_data, race, gender)

    return render_template('live_results.html', results=processed_data)

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
