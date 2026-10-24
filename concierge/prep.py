"""Preparation guidance and the power-budget timeline.

Only uses needs the customer DECLARED (never inferred).
"""
from __future__ import annotations

FRIDGE_SAFE_HOURS = 4
NEED_WORDS = {"CPAP": "CPAP", "insulin_fridge": "insulin", "oxygen": "oxygen concentrator", "sump_pump": "sump pump"}

SAFETY = [
    "Stay 35 ft from downed wires. Call 911 if you see one.",
    "Never run a generator indoors, in a garage, or near windows.",
    "Use flashlights, not candles.",
]


def actions(band: str | None, p90_hours: float | None, needs: list[dict], phase: str = "pre_storm") -> list[str]:
    if phase == "outage":
        return outage_actions(p90_hours, needs)
    out = []
    for n in needs:
        name = NEED_WORDS.get(n["need"], n["need"])
        if n["need"] == "insulin_fridge":
            out.append("Get a cooler and ice packs ready for your insulin.")
        elif n.get("battery_hours"):
            out.append(f"Charge your {name} battery tonight.")
            if p90_hours and n["battery_hours"] < p90_hours:
                out.append(f"Your outage could outlast your {name} battery. Line up a place with power.")
    out.append("Charge phones and battery packs now.")
    if (p90_hours or 0) > FRIDGE_SAFE_HOURS or band in ("High", "Severe"):
        out.append("Keep the fridge and freezer closed. Food stays cold about 4 hrs (fridge) and 48 hrs (full freezer).")
    if (p90_hours or 0) > 12:
        out.append("Have water, food that needs no cooking, and medicine for a day.")
    if (p90_hours or 0) > 24:
        out.append("Think about where you could stay if power is out over a day.")
    out.append("Fill your car's gas tank.")
    return out


def outage_actions(p90_hours: float | None, needs: list[dict]) -> list[str]:
    out = []
    for n in needs:
        name = NEED_WORDS.get(n["need"], n["need"])
        if n["need"] == "insulin_fridge":
            out.append("Move insulin to a cooler with ice packs.")
        elif n.get("battery_hours"):
            out.append(f"Switch your {name} to battery. Save it for when you need it most.")
    out.append("Keep the fridge closed.")
    out.append("Stay 35 ft from downed wires.")
    return out


def power_budget(needs: list[dict], p10: float, p90: float) -> list[dict]:
    """Line up what the customer depends on against the restoration range (hours from outage start)."""
    items = [{"hour": FRIDGE_SAFE_HOURS, "what": "Fridge stops being safe for food and insulin. Move insulin to the cooler.",
              "risk": "likely" if p10 > FRIDGE_SAFE_HOURS else "possible" if p90 > FRIDGE_SAFE_HOURS else "unlikely"}]
    for n in needs:
        bh = n.get("battery_hours")
        if bh and n["need"] != "insulin_fridge":
            name = NEED_WORDS.get(n["need"], n["need"])
            items.append({"hour": bh, "what": f"Your {name} battery runs out.",
                          "risk": "likely" if p10 > bh else "possible" if p90 > bh else "unlikely"})
    return sorted(items, key=lambda i: i["hour"])
