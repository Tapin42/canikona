import os
import json
import time
import logging
from flask import current_app, has_app_context


def full_path(*parts):
    base = os.path.dirname(__file__)
    return os.path.join(base, *parts)


# Module-level logger fallback when no Flask app context is active
_logger = logging.getLogger(__name__)


def _debug(msg: str):
    if has_app_context():
        current_app.logger.debug(msg)
    else:
        _logger.debug(msg)


def _warning(msg: str):
    if has_app_context():
        current_app.logger.warning(msg)
    else:
        _logger.warning(msg)


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def get_cache_dir(distance: str, stage: str, gender: str | None = None) -> str:
    """
    Build cache directory path.
    - distance: '140.6' or '70.3'
    - stage: 'final' or 'in_progress'
    - gender: 'men' or 'women' for 70.3
    """
    if distance == '70.3':
        if gender not in ('men', 'women'):
            # Fallback to men if missing; caller should pass correct gender
            gender = 'men'
        rel = os.path.join('data', '70.3', gender, stage)
    elif distance == '140.6':
        rel = os.path.join('data', '140.6', stage)
    else:
        # Unknown distance, keep under generic data dir
        rel = os.path.join('data', distance or 'other', stage)

    path = full_path(rel)
    ensure_dir(path)
    return path


def get_cache_file_path(race: dict, stage: str, gender: str | None = None) -> str:
    distance = race.get('distance')
    # Prefer key-based filenames; fallback to sanitized name
    key = race.get('key') or race.get('name', 'unknown').replace(' ', '_').upper()
    dirpath = get_cache_dir(distance, stage, gender)
    return os.path.join(dirpath, f"{key}.json")


def read_json_if_exists(path: str):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            _debug(f"Cache read OK: {path}")
            return data
    except FileNotFoundError:
        _debug(f"Cache read miss (not found): {path}")
        return None
    except Exception as e:
        _warning(f"Failed to read cache file {path}: {e}")
        return None


def write_json(path: str, data):
    try:
        # Ensure directory exists
        ensure_dir(os.path.dirname(path))
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")  # newline at EOF for readability
        _debug(f"Cache write OK: {path}")
    except Exception as e:
        _warning(f"Failed to write cache file {path}: {e}")


def is_fresh(path: str, freshness_seconds: int) -> bool:
    try:
        mtime = os.path.getmtime(path)
        return (time.time() - mtime) <= freshness_seconds
    except FileNotFoundError:
        _debug(f"Cache freshness miss (not found): {path}")
        return False
    except Exception as e:
        _debug(f"Cache freshness check failed for {path}: {e}")
        return False


def has_official_ag(race: dict) -> bool:
    official = race.get('results_urls', {}).get('official_ag')
    if race.get('distance') == '70.3':
        return bool(isinstance(official, dict) and (official.get('men') or official.get('women')))
    elif race.get('distance') == '140.6':
        return bool(isinstance(official, str) and official)
    return False
