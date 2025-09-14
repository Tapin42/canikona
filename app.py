import json
from flask import Flask, render_template, abort, jsonify
import parse_live_data

app = Flask(__name__)

# Function to load AG adjustments from a JSON file
def load_ag_adjustments(file_path):
    try:
        with open(file_path, 'r') as f:
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
    with open('races.json', 'r') as f:
        races = json.load(f)
    # Sort the races by date in descending order
    races.sort(key=lambda x: x['date'], reverse=True)
    return races

# A reusable function to get race data by name
def get_race_by_name(race_name):
    races = get_races()
    race_data = next((r for r in races if r['name'] == race_name), None)
    return race_data

@app.route('/')
def home():
    races = get_races()
    default_race = races[0] if races else None

    return render_template(
        'index.html',
        page_title='Long-Course Age Graded Results',
        races=races,
        selected_race=default_race['name'] if default_race else '',
        selected_source='official_ag',
        selected_gender='men'
    )

@app.route('/api/live_results/<race_name>/<gender>')
def get_live_results(race_name, gender):
    race = get_race_by_name(race_name)
    if not race:
        return jsonify({"error": "Race not found"}), 404

    if 'live' not in race.get('results_urls', {}):
        return jsonify({"error": "Live results not available for this race"}), 404

    if race['distance'] == '70.3':
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
        return jsonify({"error": processed_data["error"]}), 500

    return jsonify({"results": processed_data})

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
            coming_soon = not iframe_url

    return render_template(
        'index.html',
        page_title='Long-Course Age Graded Results',
        races=races,
        selected_race=race_name,
        selected_source=data_source,
        selected_gender=gender,
        iframe_url=iframe_url,
        coming_soon=coming_soon
    )

if __name__ == '__main__':
    context = ('ssl/domain.cert.pem', 'ssl/private.key.pem')#certificate and key files
    app.run(debug=True, ssl_context=context)