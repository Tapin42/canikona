import json
import requests
import time
import argparse
import os

# Set up argument parser
parser = argparse.ArgumentParser(description='Get weekly race details including categories and finish points from API')
parser.add_argument('input_file', nargs='?', default='./filtered_races.json',
                   help='Input JSON file (default: ./filtered_races.json)')
args = parser.parse_args()

# Determine output filename
base, ext = os.path.splitext(args.input_file)
output_file = f"{base}.out{ext}"

# Read the filtered races file
with open(args.input_file, 'r') as f:
    races_data = json.load(f)

# Base URLs for the API
CATEGORIES_URL = "https://api.rtrt.me/events/{}/categories"
POINTS_URL = "https://api.rtrt.me/events/{}/points"
CATEGORIES_PARAMS = {
    "appid": "5824c5c948fd08c23a8b4567",
    "token": "BB10EFF44090934C0EDC",
    "fields": "event_name,name,title,subtitle"
}
POINTS_PARAMS = {
    "appid": "5824c5c948fd08c23a8b4567",
    "token": "BB10EFF44090934C0EDC",
    "fields": "name,isFinish",
    "max": 100
}

# Process each race
for race in races_data['list']:
    race_id = race['name']
    print(f"Processing {race_id}...")

    # Make the API request for categories
    categories_url = CATEGORIES_URL.format(race_id)
    try:
        response = requests.get(categories_url, params=CATEGORIES_PARAMS)
        response.raise_for_status()
        categories = response.json()

        # Find the relevant category names
        live_men_cat = ""
        live_women_cat = ""
        for category in categories.get('list', []):
            title = category.get('title', '')
            subtitle = category.get('subtitle', '')
            if "Age Group Men" in title and subtitle == "Overall":
                live_men_cat = category['name']
            elif "Age Group Women" in title and subtitle == "Overall":
                live_women_cat = category['name']

        # Create the live dictionary structure
        race['live'] = {
            "men_cat": live_men_cat,
            "women_cat": live_women_cat
        }

        # Make the API request for points data
        points_url = POINTS_URL.format(race_id)
        try:
            points_response = requests.get(points_url, params=POINTS_PARAMS)
            points_response.raise_for_status()
            points_data = points_response.json()

            # Find the finish point
            finish_point_name = None
            for point in points_data.get('list', []):
                if point.get('isFinish') == '1':
                    finish_point_name = point.get('name')
                    break

            if finish_point_name is None:
                print(f"Error: No finish point found for race {race_id}")
            elif finish_point_name != 'FINISH':
                race['split'] = finish_point_name
            # If finish_point_name is 'FINISH', we don't store it (do nothing as requested)

        except Exception as e:
            print(f"Error retrieving points data for {race_id}: {str(e)}")

        # Small delay to be nice to the API
        time.sleep(0.5)

    except Exception as e:
        print(f"Error processing {race_id}: {str(e)}")
        race['live'] = {
            "men_cat": "",
            "women_cat": ""
        }

# Write the updated data back to the file
with open(output_file, 'w') as f:
    json.dump(races_data, f, indent=4)

print(f"Processing complete! Output written to {output_file}")
