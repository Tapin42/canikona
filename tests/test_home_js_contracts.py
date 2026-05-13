import pathlib


def _home_js():
    path = pathlib.Path(__file__).resolve().parents[1] / "static" / "js" / "home.js"
    return path.read_text(encoding="utf-8")


def test_home_js_handles_popstate_for_browser_navigation():
    content = _home_js()
    assert "window.addEventListener(\"popstate\"" in content
    assert "syncFromUrl(window.location.search)" in content


def test_home_js_writes_canonical_query_urls():
    content = _home_js()
    assert "new URLSearchParams()" in content
    assert "params.set(\"race\"" in content
    assert "window.history.pushState" in content
    assert "`/?${params.toString()}`" in content


def test_home_js_supports_searchable_race_picker():
    content = _home_js()
    assert "selectRaceValue" in content
    assert "raceSearch.addEventListener(\"input\"" in content
    assert "renderRaceOptions(" in content
    assert "raceDropdownToggle" in content
    assert "setCurrentRacesCollapsed(" in content
    assert "window.location.search.includes(\"race=\")" in content
    assert "updateAll(false)" in content
    assert "updateCurrentRaceSelectionUI(" in content
    assert "currentRacesHeader.addEventListener(\"click\"" in content


def test_home_js_propagates_theme_to_live_results_iframe():
    content = _home_js()
    assert "document.documentElement.getAttribute(\"data-theme\")" in content
    assert "liveUrlObj.searchParams.set(\"theme\", currentTheme)" in content


def test_home_js_reloads_live_iframe_on_theme_change_event():
    content = _home_js()
    assert "window.addEventListener(\"canikona:theme-changed\"" in content
    assert "iframe.src.includes(\"/live_results/\")" in content


def test_home_js_posts_theme_message_to_live_iframe():
    content = _home_js()
    assert "iframe.contentWindow.postMessage" in content
    assert "canikona:set-theme" in content


def test_home_js_handles_live_iframe_ready_handshake():
    content = _home_js()
    assert "window.addEventListener(\"message\"" in content
    assert "canikona:live-results-ready" in content
