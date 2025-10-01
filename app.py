import os
import json
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

# Function to read and sort race data
def get_races():
    with open(full_path('races.json'), 'r', encoding='utf-8') as f:
        races = json.load(f)

    # Get cutoff timestamp
    cutoff = int(datetime.now().timestamp())
    if current_app.debug:
        cutoff += 7 * 24 * 60 * 60  # Add 7 days in debug mode
    else:
        cutoff += 1 * 24 * 60 * 60  # Add 1 day otherwise

    # Process URLs and filter races
    filtered_races = []
    for race in races:
        if 'earliestStartTime' in race and int(race['earliestStartTime']) <= cutoff:
            # Process live result URLs if available
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

            filtered_races.append(race)

    # Sort by earliestStartTime in descending order
    filtered_races.sort(key=lambda x: int(x['earliestStartTime']), reverse=True)

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
