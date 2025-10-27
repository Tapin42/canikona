import os
import json
from datetime import datetime
from typing import Dict, Tuple, Optional
from flask import current_app

BASE_DIR = os.path.dirname(__file__)
MANIFEST_PATH = os.path.join(BASE_DIR, 'adjustments', 'manifest.json')
ASSIGNMENTS_PATH = os.path.join(BASE_DIR, 'data', 'ag_assignments.json')

# Cache containers
_manifest_cache: Optional[dict] = None
_factors_cache: Dict[str, dict] = {}
_assignments_cache: Optional[dict] = None


def _load_json(path: str) -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _save_json_atomic(path: str, data: dict) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _load_manifest() -> dict:
    global _manifest_cache
    if _manifest_cache is None:
        _manifest_cache = _load_json(MANIFEST_PATH)
    return _manifest_cache


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, '%Y-%m-%d')


def _load_assignments() -> dict:
    global _assignments_cache
    if _assignments_cache is None:
        try:
            _assignments_cache = _load_json(ASSIGNMENTS_PATH)
        except FileNotFoundError:
            _assignments_cache = {}
    return _assignments_cache


def _save_assignments(assignments: dict) -> None:
    global _assignments_cache
    _assignments_cache = assignments
    # Ensure directory exists
    os.makedirs(os.path.dirname(ASSIGNMENTS_PATH), exist_ok=True)
    _save_json_atomic(ASSIGNMENTS_PATH, assignments)


def _versions_for_distance(distance: str) -> list:
    manifest = _load_manifest()
    versions = [v for v in manifest.get('versions', []) if v.get('distance') == distance]
    # sort by effective_from ascending
    versions.sort(key=lambda v: v.get('effective_from', '1970-01-01'))
    return versions


def _select_version(distance: str, race_date: str) -> Optional[dict]:
    """
    Pick the latest version with effective_from <= race_date.
    race_date: 'YYYY-MM-DD'
    """
    try:
        r_date = _parse_date(race_date)
    except Exception:
        r_date = _parse_date('1970-01-01')
    selected = None
    for v in _versions_for_distance(distance):
        try:
            eff = _parse_date(v.get('effective_from', '1970-01-01'))
        except Exception:
            eff = _parse_date('1970-01-01')
        if eff <= r_date:
            selected = v
        else:
            break
    return selected


def _load_factors(file_path: str) -> dict:
    # Use cache key as file_path relative to BASE_DIR
    key = file_path
    if key not in _factors_cache:
        abs_path = file_path if os.path.isabs(file_path) else os.path.join(BASE_DIR, file_path)
        _factors_cache[key] = _load_json(abs_path)
    return _factors_cache[key]


def get_adjustments_for_race(race: dict) -> Tuple[dict, str]:
    """
    Return (factors_map, adjustments_version_id) for the given race.
    This function will lock the race to a specific version by recording it
    in data/ag_assignments.json under the race key.

    The recorded key is named 'adjustments_version' to be explicit.
    """
    # Determine race identity and attributes
    race_key = race.get('key') or f"{race.get('name','UNKNOWN')}-{race.get('date','UNKNOWN')}"
    distance = race.get('distance')
    race_date = race.get('date') or '1970-01-01'

    # Check existing assignment
    assignments = _load_assignments()
    existing = assignments.get(race_key)
    if isinstance(existing, dict) and 'adjustments_version' in existing:
        version_id = existing['adjustments_version']
        # find version entry
        version_entry = next((v for v in _versions_for_distance(distance) if v.get('id') == version_id), None)
        if version_entry is None:
            current_app.logger.warning(
                f"Assigned adjustments_version '{version_id}' for race {race_key} not found in manifest; falling back by date")
        else:
            factors = _load_factors(version_entry['file'])
            return factors, version_id

    # No assignment yet or invalid -> select by date rule
    version_entry = _select_version(distance, race_date)
    if version_entry is None:
        raise RuntimeError(f"No adjustments version available for distance {distance}")

    version_id = version_entry['id']
    factors = _load_factors(version_entry['file'])

    # Persist assignment
    assignments[race_key] = {
        'adjustments_version': version_id
    }
    try:
        _save_assignments(assignments)
        current_app.logger.info(f"Assigned adjustments_version '{version_id}' to race {race_key}")
    except Exception as e:
        current_app.logger.error(f"Failed to persist adjustments assignment for {race_key}: {e}")
        # Even if persistence fails, proceed with selected factors

    return factors, version_id
