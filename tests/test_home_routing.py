import pathlib
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app as app_module


def _race(name: str, distance: str, slot_policy: str, official_url):
    return {
        "name": name,
        "date": "2026-10-01",
        "distance": distance,
        "earliestStartTime": 0,
        "slots": {"men": 30, "women": 20} if distance == "70.3" else 55,
        "slot_policy": slot_policy,
        "known_rolldown": {"men": 8, "women": 6} if distance == "70.3" else 10,
        "age_group_categories": {"men": [], "women": []},
        "results_urls": {
            "live": {"men": "https://live-men", "women": "https://live-women"},
            "official_ag": official_url,
        },
    }


def _set_test_races(monkeypatch):
    races = [
        _race("Alpha 70.3", "70.3", "split-fixed", {"men": "https://official-men", "women": "https://official-women"}),
        _race("Beta 140.6", "140.6", "combined-fixed", "https://official-combined"),
    ]
    monkeypatch.setattr(app_module, "get_races", lambda: races)
    monkeypatch.setattr(app_module, "get_race_by_name", lambda race_name: next((r for r in races if r["name"] == app_module.from_url_friendly_name(race_name)), None))


def test_home_does_not_redirect_and_renders_default_race(monkeypatch):
    _set_test_races(monkeypatch)
    client = app_module.app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b'id="selection-form"' in response.data
    assert b"Alpha 70.3" in response.data


def test_home_respects_canonical_query_state(monkeypatch):
    _set_test_races(monkeypatch)
    client = app_module.app.test_client()

    response = client.get("/?race=Beta_140.6&source=official_ag")

    assert response.status_code == 200
    assert b'value="official_ag" checked' in response.data
    assert b"Beta 140.6" in response.data


def test_legacy_results_path_redirects_to_canonical_query(monkeypatch):
    _set_test_races(monkeypatch)
    client = app_module.app.test_client()

    response = client.get("/results/Alpha_70.3/live/men")

    assert response.status_code in (301, 302, 308)
    assert response.headers["Location"].endswith("/?race=Alpha_70.3&source=live&gender=men")


def test_legacy_base_results_path_redirects_with_normalized_defaults(monkeypatch):
    _set_test_races(monkeypatch)
    client = app_module.app.test_client()

    response = client.get("/results/Beta_140.6")

    assert response.status_code in (301, 302, 308)
    assert response.headers["Location"].endswith("/?race=Beta_140.6&source=official_ag")


def test_home_includes_theme_toggle_control(monkeypatch):
    _set_test_races(monkeypatch)
    client = app_module.app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b'id="theme-toggle"' in response.data


def test_controls_panel_uses_unified_section_titles_and_no_slots_duplication(monkeypatch):
    _set_test_races(monkeypatch)
    client = app_module.app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b'class="control-title">Search race<' in response.data
    assert b'class="control-title">Data Source<' in response.data
    assert b'class="control-title">Gender<' in response.data
    assert b"Current Races" in response.data
    assert b'id="slots-display"' not in response.data


def test_controls_panel_exposes_explicit_race_dropdown_browse_ui(monkeypatch):
    _set_test_races(monkeypatch)
    client = app_module.app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b'id="race-dropdown-toggle"' in response.data
    assert b'id="race-options"' in response.data


def test_live_results_route_preserves_theme_query(monkeypatch):
    _set_test_races(monkeypatch)
    client = app_module.app.test_client()

    response = client.get("/live_results/Alpha_70.3/men?theme=dark")

    assert response.status_code == 200
    assert b'data-theme="dark"' in response.data
    assert b"id=\"live-results-theme-sync\"" in response.data
    assert b"canikona:set-theme" in response.data
    assert b"canikona:live-results-ready" in response.data


def test_partition_races_groups_nearest_weekend_as_current():
    races = [
        {"name": "Sat Race", "date": "2026-05-09"},
        {"name": "Sun Race", "date": "2026-05-10"},
        {"name": "Older Race", "date": "2026-04-26"},
    ]
    current, archive = app_module.partition_races_for_controls(races, today=date(2026, 5, 7))
    assert [r["name"] for r in current] == ["Sat Race", "Sun Race"]
    assert [r["name"] for r in archive] == ["Older Race"]


def test_default_race_prefers_closest_by_date():
    races = [
        {"name": "Older Race", "date": "2026-04-20"},
        {"name": "Closest Race", "date": "2026-05-09"},
        {"name": "Farther Race", "date": "2026-06-20"},
    ]
    picked = app_module.pick_default_race(races, today=date(2026, 5, 8))
    assert picked["name"] == "Closest Race"


def test_format_current_race_label_abbreviates_ironman_names():
    assert app_module.format_current_race_label("Ironman 70.3 Gulf Coast") == "IM70.3 Gulf Coast"
    assert app_module.format_current_race_label("Ironman Texas") == "IM Texas"
