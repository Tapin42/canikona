import os
import json
from flask import Flask, render_template, abort, jsonify, redirect, url_for
from datetime import date
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
    with open(full_path('races.json'), 'r') as f:
        races = json.load(f)
# Get today's date
    today = date.today()

    # Filter out races that happen after today
    races_on_or_before_today = [
        race for race in races
        if date.fromisoformat(race['date']) <= today
    ]

    # Sort the filtered races by date in descending order
    races_on_or_before_today.sort(key=lambda x: x['date'], reverse=True)

    return races_on_or_before_today

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

    # Set up default values
    template_args = {
        'page_title': 'Long-Course Age Graded Results',
        'races': races,
        'selected_race': default_race['name'] if default_race else ''
    }

    if default_race:
        # Check if official results are available
        has_official_results = False
        if 'results_urls' in default_race and 'official_ag' in default_race['results_urls']:
            if default_race['distance'] == '70.3':
                has_official_results = bool(default_race['results_urls']['official_ag'].get('men'))
            else:
                has_official_results = bool(default_race['results_urls']['official_ag'])

        template_args['selected_source'] = 'official_ag' if has_official_results else 'live'

        # Only include gender for 70.3 races
        if default_race['distance'] == '70.3':
            template_args['selected_gender'] = 'men'
    else:
        template_args['selected_source'] = 'live'

    return render_template('index.html', **template_args)

@app.route('/about')
def about():
    return render_template('about.html')

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

    processed_data = parse_live_data.get_processed_results(race, gender, ag_adjustments)

    if "error" in processed_data:
        error_message = processed_data["msg"] if isinstance(processed_data["error"], str) and processed_data["error"] == "no_finishers" else processed_data["error"]
        return render_template('live_results.html', results=[], error=error_message)

    return render_template('live_results.html', results=processed_data)

if __name__ == '__main__':
    debug_mode = 'PYTHONANYWHERE_SITE' not in os.environ
    app.run(debug=debug_mode)
