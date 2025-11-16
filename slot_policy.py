from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

SlotPolicy = Literal["combined-fixed", "split-fixed", "split-dynamic"]


def _parse_date(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except Exception:
        return datetime(1970, 1, 1)


def resolve_slot_policy(race: dict, now: Optional[datetime] = None) -> SlotPolicy:
    """
    Decide how slots are allocated for a given race.

    Priority:
    1) If race['slot_policy'] is set to one of the allowed values, use it.
    2) Otherwise infer from structure and date:
       - If slots is a dict with 'men'/'women' -> split-fixed
       - Else if distance == '140.6' and race date >= 2025-11-14 -> split-dynamic
       - Else if distance == '70.3' -> split-fixed (back-compat)
       - Else -> combined-fixed
    """
    allowed: set[str] = {"combined-fixed", "split-fixed", "split-dynamic"}
    explicit = race.get("slot_policy")
    if isinstance(explicit, str) and explicit in allowed:
        return explicit  # explicit override

    slots = race.get("slots")
    distance = race.get("distance")

    # If structure already encodes per-gender numbers
    if isinstance(slots, dict) and ("men" in slots or "women" in slots):
        return "split-fixed"

    # Heuristic by distance/date for 140.6
    if distance == "140.6":
        race_date = race.get("date") or "1970-01-01"
        boundary = _parse_date("2025-11-14")
        r_date = _parse_date(race_date)
        if r_date >= boundary:
            return "split-dynamic"
        return "combined-fixed"

    # 70.3 default remains split-fixed
    if distance == "70.3":
        return "split-fixed"

    # Fallback
    return "combined-fixed"


def policy_needs_gender(policy: SlotPolicy) -> bool:
    return policy in ("split-fixed", "split-dynamic")


def is_split(policy: SlotPolicy) -> bool:
    return policy in ("split-fixed", "split-dynamic")
