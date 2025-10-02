import os
import json
import math
from flask import Flask, render_template, abort, jsonify, redirect, url_for, current_app
from datetime import date, datetime, timedelta
import parse_live_data

app = Flask(__name__)

def full_path(relative_path):
    return os.path.join(os.path.dirname(__file__), relative_path)

# Function to load AG adjustments from a JSON file
def load_ag_adjustments(file_path):
    try:
        with open(full_path(file_path), 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: Age-graded adjustments file not found at '{file_path}'.")
        raise
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON format in '{file_path}'.")
        raise

# Load AG adjustments at app startup
AG_ADJUSTMENTS_703 = load_ag_adjustments('ag_adjustments_703.json')
AG_ADJUSTMENTS_1406 = load_ag_adjustments('ag_adjustments_1406.json')

# Function to load and process all races at startup
def load_and_process_races():
    with open(full_path('races.json'), 'r', encoding='utf-8') as f:
        races = json.load(f)

    # Process URLs for all races
    for race in races:
        if ('results_urls' in race and 'live' in race['results_urls'] and
            isinstance(race['results_urls']['live'], dict) and 'key' in race):
            live = race['results_urls']['live']

            # Process men's URL
            if 'men_cat' in live:
                men_url = f"https://api.rtrt.me/events/{race['key']}/categories/{live['men_cat']}/splits/FINISH"
                live['men'] = men_url

            # Process women's URL
            if 'women_cat' in live:
                women_url = f"https://api.rtrt.me/events/{race['key']}/categories/{live['women_cat']}/splits/FINISH"
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

# Function to get filtered race data (already sorted at startup)
def get_races():
    # Filter the pre-loaded races data
    filtered_races = filter_races_by_timestamp(ALL_RACES, current_app.debug)

    return filtered_races

# Functions to convert between display names and URL-friendly names
def to_url_friendly_name(race_name):
    return race_name.replace(' ', '_')

def from_url_friendly_name(url_name):
    return url_name.replace('_', ' ')

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
                             selected_race='',
                             debug_mode=app.debug)

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

    # Check if official_ag results are available
    has_official_ag = False
    if 'results_urls' in race and 'official_ag' in race['results_urls']:
        if race['distance'] == '70.3':
            has_official_ag = bool(race['results_urls']['official_ag'].get('men'))
        else:  # 140.6
            has_official_ag = bool(race['results_urls']['official_ag'])

    # Determine redirect URL based on race distance and availability of official_ag
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

    debug_mode = app.debug

    return render_template(
        'index.html',
        page_title='Long-Course Age Graded Results',
        races=races,
        selected_race=from_url_friendly_name(race_name),
        selected_source=data_source,
        selected_gender=gender,
        iframe_url=iframe_url,
        coming_soon=coming_soon,
        debug_mode=debug_mode
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
        ag_adjustments = AG_ADJUSTMENTS_703
    elif race['distance'] == '140.6':
        if 'men' not in race['results_urls']['live'] or 'women' not in race['results_urls']['live']:
            return jsonify({"error": "Live results URLs for both men and women must be provided for 140.6 races"}), 404
        ag_adjustments = AG_ADJUSTMENTS_1406
    else:
        return jsonify({"error": f"Invalid race distance: {race['distance']}"}), 400

    # Check if we should fetch results based on race timing
    message, should_fetch_results = get_race_status_message(race)
    if not should_fetch_results:
        return render_template('live_results.html', results=[], error=message)

    processed_data = parse_live_data.get_processed_results(race, gender, ag_adjustments)

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

    return render_template('live_results.html', results=processed_data)

if __name__ == '__main__':
    debug_mode = 'PYTHONANYWHERE_SITE' not in os.environ
    app.run(debug=debug_mode)
