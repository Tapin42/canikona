#!/usr/bin/env python3
"""
Validate that JSON files are:
- Valid UTF-8 (no invalid byte sequences; BOM tolerated and stripped)
- Valid JSON (parsable)
- Free of unpaired surrogate code points in any string value

Usage:
  python3 scripts/validate_json_unicode.py <file1.json> <file2.json> ...

Exit codes:
  0 - all files valid or no files provided
  1 - one or more files invalid
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Iterable, List, Tuple

SURROGATE_MIN = 0xD800
SURROGATE_MAX = 0xDFFF


def _strip_utf8_bom(data: bytes) -> bytes:
    # UTF-8 BOM is EF BB BF
    if data.startswith(b"\xEF\xBB\xBF"):
        return data[3:]
    return data


def find_surrogates_in_string(s: str) -> List[Tuple[int, str]]:
    """Return list of (index, char) for any surrogate code points in s."""
    out: List[Tuple[int, str]] = []
    for idx, ch in enumerate(s):
        cp = ord(ch)
        if SURROGATE_MIN <= cp <= SURROGATE_MAX:
            out.append((idx, ch))
    return out


def traverse_for_bad_unicode(obj: Any, path: List[str] | None = None) -> List[str]:
    """Traverse JSON object and report any unpaired surrogate code points in strings.

    Returns a list of human-readable error messages.
    """
    if path is None:
        path = []
    errors: List[str] = []

    if isinstance(obj, str):
        surros = find_surrogates_in_string(obj)
        if surros:
            loc = ".".join(path) if path else "<root>"
            indexes = ", ".join(f"{i} (U+{ord(ch):04X})" for i, ch in surros)
            errors.append(
                f"unpaired surrogate(s) at {loc}: indexes {indexes}. JSON strings must not contain code points in U+D800..U+DFFF"
            )
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            errors.extend(traverse_for_bad_unicode(v, path + [f"[{i}]"]))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            key_path_component = str(k)
            # Also validate keys are free of surrogates
            if isinstance(k, str):
                surros = find_surrogates_in_string(k)
                if surros:
                    indexes = ", ".join(f"{i} (U+{ord(ch):04X})" for i, ch in surros)
                    errors.append(
                        f"unpaired surrogate(s) in key '{k}': indexes {indexes}"
                    )
            errors.extend(traverse_for_bad_unicode(v, path + [key_path_component]))
    # other types are fine
    return errors


def validate_file(path: str) -> List[str]:
    """Return list of errors for a given JSON file path."""
    errs: List[str] = []
    if not os.path.exists(path):
        # Deleted or moved file in git
        return errs
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception as e:
        errs.append(f"failed to read file: {e}")
        return errs

    raw = _strip_utf8_bom(raw)

    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as e:
        errs.append(f"invalid UTF-8: {e}")
        return errs

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        # Preserve location info
        errs.append(f"invalid JSON: {e}")
        return errs

    errs.extend(traverse_for_bad_unicode(data))
    return errs


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate JSON files for UTF-8 and Unicode correctness")
    parser.add_argument("files", nargs="*", help="JSON files to validate")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print success messages for valid files")
    args = parser.parse_args(list(argv) if argv is not None else None)

    files = [f for f in args.files if f.endswith(".json")]
    if not files:
        return 0

    had_errors = False
    for fpath in files:
        errors = validate_file(fpath)
        if errors:
            had_errors = True
            rel = os.path.relpath(fpath)
            print(f"✗ {rel}", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
        elif args.verbose:
            print(f"✓ {fpath}")

    if had_errors:
        print("\nJSON Unicode validation failed. Fix the above issues or commit with --no-verify to bypass (not recommended).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
