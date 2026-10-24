"""Generate a fake dataset shaped like the AEP challenge extracts.

Run:  python -m concierge.mockdata

Writes CSV/TXT/JSON files to data/mock/. The real package uses shapefiles and
Excel; the Data teammate's job is to load those into these same tables so the
rest of the code doesn't change.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .config import MOCK_DIR

SEED = 7

# STORMS-style reference: labor hours per unit of damage (made up, plausible).
LABOR_HOURS = {
    "pole_replace": 6.0,
    "crossarm_replace": 3.0,
    "insulator_replace": 1.5,
    "conductor_repair": 2.5,  # per span
    "tree_removal": 2.0,
    "transformer_replace": 4.0,
    "fuse_replace": 0.75,
}
INDIRECT = {"travel": 1.25, "nonproductive": 1.15}

ASSET_DAMAGE = {
    "pole": ["pole_replace", "crossarm_replace", "insulator_replace"],
    "conductor": ["conductor_repair", "tree_removal"],
    "transformer": ["transformer_replace"],
    "protective_device": ["fuse_replace"],
}
# Which asset types tend to fail in each zone (zone = section below a device).
ZONE_ASSETS = {
    "BRK": (["pole", "conductor"], [0.5, 0.5]),
    "REC": (["pole", "conductor", "protective_device"], [0.4, 0.45, 0.15]),
    "FUSE": (["pole", "conductor", "protective_device"], [0.3, 0.5, 0.2]),
    "XFMR": (["transformer", "conductor"], [0.6, 0.4]),
}
ZONE_SIZE = {"BRK": 0.6, "REC": 0.3, "FUSE": 0.0, "XFMR": -1.4}
DEPTH = {"SUB": 0, "BRK": 1, "REC": 2, "FUSE": 3, "XFMR": 4}

N_CIRCUITS = 14
NEW_CIRCUIT = "C13"  # only one storm of history -> exercises the fallback
DEMO_CIRCUIT = "C14"
DEMO_EVENT = "E07"


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def build_network(rng):
    circuits, devices, premises, topo_lines = [], [], [], []
    used_ids = set()
    for c in range(1, N_CIRCUITS + 1):
        cid = f"C{c:02d}"
        sub = f"S{(c - 1) // 4 + 1}"
        vuln = rng.uniform(0, 1)
        circuits.append({
            "circuit_id": cid,
            "substation_id": sub,
            "region": "R-North" if c <= 7 else "R-South",
            "district": f"D{(c - 1) // 3 + 1}",
            "overhead_pct": round(float(np.clip(0.45 + 0.45 * vuln + rng.normal(0, 0.08), 0.3, 0.98)), 2),
            "length_mi": round(float(rng.uniform(3, 15)), 1),
            "_vuln": vuln,
        })
        brk = f"{cid}-B"
        devices.append({"device_id": brk, "device_type": "BRK", "circuit_id": cid, "parent_id": sub})
        for r in range(1, 3):
            rec = f"{cid}-R{r}"
            devices.append({"device_id": rec, "device_type": "REC", "circuit_id": cid, "parent_id": brk})
            for f in range(1, 3):
                fuse = f"{rec}-F{f}"
                devices.append({"device_id": fuse, "device_type": "FUSE", "circuit_id": cid, "parent_id": rec})
                for t in range(1, 4):
                    xf = f"{fuse}-T{t}"
                    devices.append({"device_id": xf, "device_type": "XFMR", "circuit_id": cid, "parent_id": fuse})
                    for _ in range(4):
                        while True:
                            pid = f"P{rng.integers(10000, 99999)}"
                            if pid not in used_ids:
                                used_ids.add(pid)
                                break
                        premises.append({"premise_id": pid, "circuit_id": cid, "hex_id": f"hex-{c:02d}-{r}{f}"})
                        topo_lines.append(f"{pid}|{sub}>{brk}>{rec}>{fuse}>{xf}")
    return circuits, devices, premises, topo_lines


def schedule_truth(jobs, crews, rng):
    """Assign jobs to crews upstream-first; returns finish time (hours) per job."""
    order = sorted(range(len(jobs)), key=lambda i: (jobs[i]["depth"], jobs[i]["avail"]))
    crew_free = [0.0] * crews
    finish = [0.0] * len(jobs)
    for i in order:
        j = jobs[i]
        dur = LABOR_HOURS[j["damage_type"]] * j["quantity"] * INDIRECT["travel"] * INDIRECT["nonproductive"]
        dur *= rng.lognormal(0, 0.35)
        k = int(np.argmin(crew_free))
        start = max(crew_free[k], j["avail"])
        crew_free[k] = start + dur
        finish[i] = crew_free[k]
    return finish


def make_job(rng, device, zone, report_h):
    assets, w = ZONE_ASSETS[zone]
    asset = rng.choice(assets, p=w)
    dtype = rng.choice(ASSET_DAMAGE[asset])
    qty = int(rng.integers(1, 4)) if dtype in ("pole_replace", "conductor_repair") else 1
    return {"device_id": device, "asset_type": asset, "damage_type": dtype, "quantity": qty,
            "depth": DEPTH[zone], "report_h": report_h}


def main():
    rng = np.random.default_rng(SEED)
    MOCK_DIR.mkdir(parents=True, exist_ok=True)
    circuits, devices, premises, topo_lines = build_network(rng)
    dev_df = pd.DataFrame(devices)

    storms = []
    base = datetime(2021, 3, 1, 14, 0)
    for e in range(1, 11):
        storms.append({
            "event_id": f"E{e:02d}",
            "name": ["Ash", "Birch", "Cedar", "Dogwood", "Elm", "Fir", "Ginkgo", "Hazel", "Ivy", "Juniper"][e - 1] + " windstorm",
            "start_time": (base + timedelta(days=int(170 * e + rng.integers(0, 40)))).isoformat(timespec="minutes"),
            "max_wind_mph": int(rng.integers(35, 80)),
            "ice_in": round(float(rng.choice([0, 0, 0, 0.1, 0.25, 0.5])), 2),
        })
    storms[6]["max_wind_mph"] = 72  # demo storm E07 is a big one
    storms[6]["ice_in"] = 0.0

    rows = []
    inc = 1
    for s in storms:
        start = datetime.fromisoformat(s["start_time"])
        for c in circuits:
            cid = c["circuit_id"]
            if cid == NEW_CIRCUIT and s["event_id"] != "E10":
                continue
            jobs = []
            if not (cid == DEMO_CIRCUIT and s["event_id"] == DEMO_EVENT):
                for _, d in dev_df[dev_df.circuit_id == cid].iterrows():
                    zone = d.device_type
                    logit = (-5.0 + 0.07 * (s["max_wind_mph"] - 40) + 3.0 * s["ice_in"]
                             + 2.2 * c["_vuln"] + 1.2 * c["overhead_pct"] + ZONE_SIZE[zone])
                    if rng.random() < sigmoid(logit):
                        n = 1 + rng.poisson(max(0.1, 0.3 + 0.02 * (s["max_wind_mph"] - 40)))
                        for _ in range(n):
                            report_h = float(rng.gamma(2.0, 1.5)) + (rng.random() < 0.15) * rng.uniform(4, 10)
                            jobs.append(make_job(rng, d.device_id, zone, report_h))
            else:
                # Scripted damage for Maria's demo (her path: C14-B > C14-R1 > C14-R1-F2 > ...-T1)
                jobs = [
                    {"device_id": "C14-B", "asset_type": "conductor", "damage_type": "conductor_repair", "quantity": 1, "depth": 1, "report_h": 1.0},
                    {"device_id": "C14-R1", "asset_type": "pole", "damage_type": "pole_replace", "quantity": 1, "depth": 2, "report_h": 1.5},
                    {"device_id": "C14-R2-F1", "asset_type": "conductor", "damage_type": "tree_removal", "quantity": 1, "depth": 3, "report_h": 2.0},
                    {"device_id": "C14-R1-F2", "asset_type": "pole", "damage_type": "pole_replace", "quantity": 1, "depth": 3, "report_h": 6.0},
                    {"device_id": "C14-R2-F2-T3", "asset_type": "transformer", "damage_type": "transformer_replace", "quantity": 1, "depth": 4, "report_h": 3.0},
                ]
            if not jobs:
                continue
            crews = max(1, int(round(4 - (s["max_wind_mph"] - 40) / 20 + rng.normal(0, 0.5))))
            if cid == DEMO_CIRCUIT and s["event_id"] == DEMO_EVENT:
                crews = 2
            for j in jobs:
                j["avail"] = j["report_h"] + rng.uniform(1, 3)  # assessment before work starts
            finish = schedule_truth(jobs, crews, rng)
            for j, f in zip(jobs, finish):
                rows.append({
                    "incident_id": f"INC{inc:05d}",
                    "event_id": s["event_id"],
                    "region": c["region"],
                    "district": c["district"],
                    "substation_id": c["substation_id"],
                    "circuit_id": cid,
                    "device_id": j["device_id"],
                    "asset_type": j["asset_type"],
                    "damage_type": j["damage_type"],
                    "quantity": j["quantity"],
                    "crew_count": crews,
                    "report_time": (start + timedelta(hours=j["report_h"])).isoformat(timespec="minutes"),
                    "repair_complete_time": (start + timedelta(hours=f)).isoformat(timespec="minutes"),
                    "repair_status": "complete",
                })
                inc += 1

    pd.DataFrame([{k: v for k, v in c.items() if not k.startswith("_")} for c in circuits]).to_csv(MOCK_DIR / "circuits.csv", index=False)
    dev_df.to_csv(MOCK_DIR / "devices.csv", index=False)
    pd.DataFrame(premises).to_csv(MOCK_DIR / "premises.csv", index=False)
    (MOCK_DIR / "topology.txt").write_text("# premise_id|upline devices, substation first\n" + "\n".join(topo_lines) + "\n")
    pd.DataFrame(storms).to_csv(MOCK_DIR / "storms.csv", index=False)
    pd.DataFrame(rows).to_csv(MOCK_DIR / "arcos_damage.csv", index=False)
    pd.DataFrame([{"damage_type": k, "labor_hours_per_unit": v} for k, v in LABOR_HOURS.items()]).to_csv(MOCK_DIR / "labor_hours.csv", index=False)
    pd.DataFrame([{"factor": k, "multiplier": v} for k, v in INDIRECT.items()]).to_csv(MOCK_DIR / "indirect_labor.csv", index=False)
    pd.DataFrame([
        {"facility_id": "F001", "facility_type": "hospital", "circuit_id": "C03", "device_id": "C03-R1-F1-T2"},
        {"facility_id": "F002", "facility_type": "fire_department", "circuit_id": "C14", "device_id": "C14-R2-F1-T1"},
        {"facility_id": "F003", "facility_type": "ambulance", "circuit_id": "C08", "device_id": "C08-R2-F2-T3"},
    ]).to_csv(MOCK_DIR / "priority_locations.csv", index=False)

    maria_premise = next(l.split("|")[0] for l in topo_lines if ">C14-R1-F2-T1" in l)
    new_premise = next(p["premise_id"] for p in premises if p["circuit_id"] == NEW_CIRCUIT)
    low_premise = next(l.split("|")[0] for l in topo_lines if ">C05-R1-F1-T1" in l)
    customers = [
        {"phone": "+15550100001", "name": "Maria", "premise_id": maria_premise, "alerts_opt_in": True,
         "declared_needs": [{"need": "CPAP", "battery_hours": 8}], "language": "en"},
        {"phone": "+15550100002", "name": "Dev", "premise_id": new_premise, "alerts_opt_in": True,
         "declared_needs": [], "language": "en"},
        {"phone": "+15550100003", "name": "Sam", "premise_id": low_premise, "alerts_opt_in": False,
         "declared_needs": [{"need": "insulin_fridge", "battery_hours": 4}], "language": "en"},
    ]
    (MOCK_DIR / "customers.json").write_text(json.dumps(customers, indent=2))
    (MOCK_DIR / "current_storm.json").write_text(json.dumps({
        "name": "Storm Kestrel", "eta_hours": 18, "max_wind_mph": 66, "ice_in": 0.0,
        "replay_of_event": DEMO_EVENT,
        "note": "Forecast for the demo. 'replay_of_event' is the past storm we replay once the outage starts.",
    }, indent=2))
    print(f"Wrote {len(rows)} damage records, {len(premises)} premises, {len(storms)} storms to {MOCK_DIR}")
    print(f"Maria's premise: {maria_premise}; fallback premise (new circuit): {new_premise}")


if __name__ == "__main__":
    main()
