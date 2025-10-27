#!/usr/bin/env python3
import os
import json
import argparse
from datetime import datetime
from typing import List, Dict

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
MANIFEST_PATH = os.path.join(BASE_DIR, 'adjustments', 'manifest.json')
ASSIGNMENTS_PATH = os.path.join(BASE_DIR, 'data', 'ag_assignments.json')
RACES_PATH = os.path.join(BASE_DIR, 'races.json')


def load_json(path: str, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def save_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def versions_for_distance(manifest: dict, distance: str) -> List[dict]:
    vs = [v for v in manifest.get('versions', []) if v.get('distance') == distance]
    vs.sort(key=lambda v: v.get('effective_from', '1970-01-01'))
    return vs


def select_version(manifest: dict, distance: str, race_date: str) -> dict | None:
    try:
        r_date = datetime.strptime(race_date, '%Y-%m-%d')
    except Exception:
        r_date = datetime(1970, 1, 1)
    selected = None
    for v in versions_for_distance(manifest, distance):
        try:
            eff = datetime.strptime(v.get('effective_from', '1970-01-01'), '%Y-%m-%d')
        except Exception:
            eff = datetime(1970, 1, 1)
        if eff <= r_date:
            selected = v
        else:
            break
    return selected


def cmd_list_versions(args):
    m = load_json(MANIFEST_PATH)
    for v in m.get('versions', []):
        print(f"{v['id']:20} dist={v['distance']:5} effective_from={v['effective_from']} file={v['file']}")


def cmd_list_assignments(args):
    a = load_json(ASSIGNMENTS_PATH, default={})
    if not a:
        print('(no assignments)')
        return
    for k, v in a.items():
        if isinstance(v, dict) and isinstance(v.get('per_distance'), dict):
            mapping = ", ".join(f"{d}={vid}" for d, vid in sorted(v['per_distance'].items()))
            print(f"{k}: {mapping}")
        else:
            print(f"{k}: {v.get('adjustments_version')}")


def cmd_dry_run(args):
    m = load_json(MANIFEST_PATH)
    races: List[Dict] = load_json(RACES_PATH)
    missing = 0
    for r in races:
        key = r.get('key') or f"{r.get('name')}-{r.get('date')}"
        ver = select_version(m, r.get('distance'), r.get('date'))
        if ver is None:
            print(f"NO VERSION for {key} ({r.get('distance')} on {r.get('date')})")
            missing += 1
        else:
            print(f"{key}: {ver['id']}")
    if missing:
        print(f"Missing versions for {missing} races")


def cmd_write(args):
    m = load_json(MANIFEST_PATH)
    races: List[Dict] = load_json(RACES_PATH)
    a = load_json(ASSIGNMENTS_PATH, default={})
    updates = 0
    for r in races:
        key = r.get('key') or f"{r.get('name')}-{r.get('date')}"
        ver = select_version(m, r.get('distance'), r.get('date'))
        if ver is None:
            continue
        # Initialize entry and per_distance mapping
        entry = a.get(key)
        if not isinstance(entry, dict):
            entry = {}
        per_distance = entry.get('per_distance')
        if not isinstance(per_distance, dict):
            per_distance = {}
        # If not overwriting and value exists for this distance, skip
        if not args.overwrite and r.get('distance') in per_distance:
            a[key] = entry
            continue
        per_distance[r.get('distance')] = ver['id']
        entry['per_distance'] = per_distance
        # Remove legacy field if present
        if 'adjustments_version' in entry:
            entry.pop('adjustments_version', None)
        a[key] = entry
        updates += 1
    save_json(ASSIGNMENTS_PATH, a)
    print(f"Wrote assignments for {updates} races -> {ASSIGNMENTS_PATH}")


def main():
    p = argparse.ArgumentParser(description='Manage versioned AG adjustments')
    sub = p.add_subparsers(dest='cmd')

    sub.add_parser('list-versions').set_defaults(func=cmd_list_versions)
    sub.add_parser('list-assignments').set_defaults(func=cmd_list_assignments)
    sub.add_parser('dry-run').set_defaults(func=cmd_dry_run)
    w = sub.add_parser('write-assignments')
    w.add_argument('--overwrite', action='store_true', help='overwrite existing assignments')
    w.set_defaults(func=cmd_write)

    args = p.parse_args()
    if not hasattr(args, 'func'):
        p.print_help()
        return
    args.func(args)


if __name__ == '__main__':
    main()
