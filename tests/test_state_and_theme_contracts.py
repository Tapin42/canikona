import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app as app_module


def test_stylesheet_declares_light_and_dark_theme_tokens():
    css = pathlib.Path(__file__).resolve().parents[1] / "static" / "css" / "styles.css"
    content = css.read_text(encoding="utf-8")

    assert ":root {" in content
    assert "html[data-theme=\"dark\"]" in content
    assert "--bg:" in content
    assert "--surface:" in content
    assert "--accent:" in content


def test_stylesheet_hides_backing_race_select_for_searchable_picker():
    css = pathlib.Path(__file__).resolve().parents[1] / "static" / "css" / "styles.css"
    content = css.read_text(encoding="utf-8")

    assert "#race-select" in content
    assert "position: absolute" in content
    assert "opacity: 0" in content
    assert "pointer-events: none" in content


def test_loading_overlay_uses_theme_tokens_not_hardcoded_white():
    css = pathlib.Path(__file__).resolve().parents[1] / "static" / "css" / "styles.css"
    content = css.read_text(encoding="utf-8")

    assert "--loading-overlay:" in content
    assert "--spinner-track:" in content
    assert "--spinner-head:" in content
    assert "background: var(--loading-overlay)" in content
    assert "border: 8px solid var(--spinner-track)" in content
    assert "border-top: 8px solid var(--spinner-head)" in content


def test_slot_summary_panel_uses_theme_tokens():
    css = pathlib.Path(__file__).resolve().parents[1] / "static" / "css" / "styles.css"
    content = css.read_text(encoding="utf-8")

    assert ".slot-summary {" in content
    assert "background-color: var(--surface)" in content
    assert "border: 1px solid var(--border)" in content


def test_row_highlights_target_cells_not_row_background_only():
    css = pathlib.Path(__file__).resolve().parents[1] / "static" / "css" / "styles.css"
    content = css.read_text(encoding="utf-8")

    assert ".row-ag-winner td {" in content
    assert ".row-pool-qualifier td {" in content
    assert "html[data-theme=\"dark\"] .row-ag-winner td {" in content
    assert "html[data-theme=\"dark\"] .row-pool-qualifier td {" in content


def test_toggle_labels_use_tokenized_high_contrast_colors():
    css = pathlib.Path(__file__).resolve().parents[1] / "static" / "css" / "styles.css"
    content = css.read_text(encoding="utf-8")

    assert "--chip-ag-bg:" in content
    assert "--chip-ag-text:" in content
    assert "--chip-pool-bg:" in content
    assert "--chip-pool-text:" in content
    assert "background: var(--chip-ag-bg)" in content
    assert "color: var(--chip-ag-text)" in content
    assert "background: var(--chip-pool-bg)" in content
    assert "color: var(--chip-pool-text)" in content


def test_current_race_cards_have_active_and_non_overlapping_styles():
    css = pathlib.Path(__file__).resolve().parents[1] / "static" / "css" / "styles.css"
    content = css.read_text(encoding="utf-8")

    assert ".current-race-btn.active {" in content
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in content
    assert ".current-race-btn {" in content and "overflow-wrap: anywhere" in content
    assert ".archive-dropdown {" in content
    assert "top: calc(100% + 8px);" in content
    assert ".selection-group:first-of-type" not in content
    assert "width: 100%;" in content
    assert "text-align: center;" in content


def test_normalize_selection_falls_back_from_unavailable_official_source():
    race = {
        "distance": "70.3",
        "slot_policy": "split-fixed",
        "results_urls": {
            "live": {"men": "https://live-men", "women": "https://live-women"},
            "official_ag": {"men": "", "women": ""},
        },
        "earliestStartTime": 0,
    }

    selection = app_module.normalize_selection(race, source="official_ag", gender="women")

    assert selection["source"] == "live"
    assert selection["gender"] == "women"


def test_normalize_selection_drops_gender_when_not_required():
    race = {
        "distance": "140.6",
        "slot_policy": "combined-fixed",
        "results_urls": {"live": {"men": "m", "women": "w"}, "official_ag": "https://official"},
        "earliestStartTime": 0,
    }

    selection = app_module.normalize_selection(race, source="official_ag", gender="women")

    assert selection["source"] == "official_ag"
    assert selection["gender"] is None
