"""Convert the AEP challenge package into the tables the engines read.

Run (after extracting the archives as described in the package's data/README.md):
    python -m concierge.realdata [--src aep_challenge_hack26-main/data] [--out data/real]

Writes the same files as concierge/mockdata.py, built from the real extracts:
  ARCOS damage shapefile     -> arcos_damage.csv, storms.csv
  Customer + matched_upstream -> premises.csv, devices.csv, topology.txt
  Primary lines              -> circuits.csv (length, overhead share)
  Install_Labor workbook     -> labor_hours.csv, labor_basis.csv, indirect_labor.csv
  Hospitals / fire / EMS     -> priority_locations.csv (device + circuit only, no coordinates or names)
plus synthetic personas (customers.json), the demo forecast (current_storm.json)
and conversion_report.json, which lists every assumption and every dropped record.

Privacy: customer names, addresses, road names, coordinates and the life-support flag
are never read into the output. Coordinates are only used here, to place damage
on the network and to split each circuit into four coarse map areas.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
import pyogrio
from sklearn.neighbors import BallTree

from .config import ROOT

SRC = ROOT / "aep_challenge_hack26-main" / "data"
OUT = ROOT / "data" / "real"

STORM_GAP_DAYS = 3      # reports more than 3 days apart belong to different events
MIN_STORM_RECORDS = 10  # smaller clusters are everyday damage, not storms
MAX_REPAIR_DAYS = 7     # repairs logged later than this are follow-up work, not restoration
REPORT_HOUR, REPAIR_HOUR = 6, 18  # ARCOS has dates only; we place reports at 06:00 and repairs at 18:00
CREW_SIZE = 4           # workbook hours are person-hours; a crew of 4 works them in parallel
FACILITY_MAX_M = 400    # a facility farther than this from any transformer isn't on our circuits

# matched_upstream "type" field -> our device types
TOPO_TYPES = {"Distribution": "XFMR", "Step": "STEP", "Fuse": "FUSE", "Recloser (Hydraulic)": "REC",
              "Recloser (Electronic)": "REC", "Sectionalizer": "SECT", "Circuit Breaker": "BRK"}
ID_PREFIX = {"XFMR": "X", "STEP": "T", "FUSE": "F", "REC": "R", "SECT": "S", "BRK": "B"}
PROTECTIVE = {"BRK", "REC", "SECT", "FUSE"}

# ARCOS asset type -> (damage_type, where it is placed on the network)
#   "self":  the nearest device of that kind
#   "xfmr":  the nearest transformer (only its customers lose power)
#   "zone":  the protective device above the nearest transformer (a primary fault trips it)
ASSET_DAMAGE = {
    "Pole": ("pole_replace", "zone"),
    "primary": ("conductor_repair", "zone"),
    "Switch": ("switch_repair", "zone"),
    "Capacitor Bank": ("equipment_repair", "zone"),
    "Step Transformer": ("equipment_repair", "zone"),
    "Fuse Bank": ("fuse_replace", "self:FUSE"),
    "Recloser Bank": ("recloser_replace", "self:REC"),
    "Transformer": ("transformer_replace", "xfmr"),
    "secondary": ("secondary_repair", "xfmr"),
    "Light": ("light_repair", "xfmr"),
}
INFORM_FALLBACK = {"Damaged Pole": "Pole", "Damaged Wire": "primary", "Damaged Switch": "Switch",
                   "Damaged Equipment": "Transformer", "Damaged Light": "Light"}

# Workbook "Labor only work" adders (person-hours) triggered by ARCOS access / equipment fields
ACCESS_ADDER = 0.5                 # Access Constraint Adder
EQUIPMENT_ADDER = {"Backyard Bucket": 0.5, "Track Hoe": 1.312, "Yard Dog": 1.312}  # confined / remote access
DEFAULT_EQUIPMENT_ADDER = 0.5      # Specialized Handling Adder for any other special equipment


def _layer(src: Path, name: str, columns: list[str], geometry: bool = False) -> pd.DataFrame:
    path = next((src / "extracted").glob(f"*/{name}.shp"))
    df = pyogrio.read_dataframe(path, columns=columns, read_geometry=geometry)
    if geometry:
        if df.crs and df.crs.to_epsg() != 4326:
            df = df.to_crs(4326)
        df = pd.DataFrame({**{c: df[c] for c in columns}, "lon": df.geometry.x, "lat": df.geometry.y})
    return df


# ---------- labor workbook ----------

def _section(ws, title: str) -> list[tuple]:
    rows = [tuple(c for c in r if c is not None) for r in ws.iter_rows(values_only=True)]
    i = next(k for k, r in enumerate(rows) if r and r[0] == title)
    out = []
    for r in rows[i + 2:]:
        if not r or (isinstance(r[0], str) and "Average Hours" in r[0]):
            break
        out.append(r)
    return out


def labor_tables(xlsx: Path) -> tuple[dict, pd.DataFrame, dict]:
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    ws = wb["Labor time material related"]

    def avg(title):  # install + removal person-hours for one compatible unit (CU)
        inst, rem = _section(ws, title)[0][-2:]
        return float(inst) + float(rem)

    pole = {r[0]: float(r[-2]) + float(r[-1]) for r in _section(ws, "Pole CU Average Hours by Size")}
    xfmr = {int(r[0]): float(r[-2]) + float(r[-1]) for r in _section(ws, "Transformer CU Average Hours by Size")}
    cnc = list(wb["CNC"].iter_rows(values_only=True))  # connector averages sit beside the CU list
    i, row = next((i, r) for i, r in enumerate(cnc) if "Avg Install Hours" in r)
    col = row.index("Avg Install Hours")
    connector = float(cnc[i + 1][col]) + float(cnc[i + 1][col + 1])
    cu = {"pole_medium": pole["Medium"], "crossarm": avg("Crossarm CU Average Hours"),
          "bracket": avg("Bracket CU Average Hours"), "insulator": avg("Insulator CU Average Hours"),
          "pin": avg("Pin CU Average Hours"), "guying": avg("Guying CU Average Hours"),
          "anchor": avg("Anchor CU Average Hours"), "grounding": avg("Grounding CU Average Hours"),
          "dead_end": avg("Dead-End CU Average Hours"), "equipment_lead": avg("Equipment Lead CU Average Hours"),
          "recloser": avg("Recloser CU Average Hours"), "transformer_25kva": xfmr[25], "connector": connector}
    # What one repair of each kind replaces, in compatible units (our assumption, see labor_basis.csv).
    recipes = {
        "pole_replace": {"pole_medium": 1, "crossarm": 1, "insulator": 3, "pin": 3, "guying": 1, "anchor": 1, "grounding": 1},
        "conductor_repair": {"dead_end": 2, "connector": 4, "insulator": 2},
        "secondary_repair": {"dead_end": 1, "connector": 2},
        "transformer_replace": {"transformer_25kva": 1, "equipment_lead": 1, "grounding": 1},
        "fuse_replace": {"equipment_lead": 1, "bracket": 1},
        "recloser_replace": {"recloser": 1, "equipment_lead": 2, "bracket": 1},
        "switch_repair": {"equipment_lead": 2, "bracket": 1},
        "equipment_repair": {"equipment_lead": 2, "bracket": 1},
        "light_repair": {"bracket": 1},
    }
    hours = {k: round(sum(cu[u] * n for u, n in r.items()), 3) for k, r in recipes.items()}
    basis = pd.DataFrame([{"damage_type": k, "person_hours": hours[k],
                           "recipe": " + ".join(f"{n} x {u} ({cu[u]:.3f} h)" for u, n in r.items())}
                          for k, r in recipes.items()])
    ilm = float(next(r[1] for r in wb["Indirect Labor Multiplier"].iter_rows(values_only=True) if r and r[1]))
    indirect = {"indirect_labor_multiplier": ilm, f"crew_of_{CREW_SIZE}": round(1 / CREW_SIZE, 4)}
    return hours, basis, indirect


# ---------- network ----------

def load_network(src: Path):
    """Parse matched_upstream.txt into premise -> upline path (substation first)."""
    fuse = _layer(src, "Fuse_Bank_Clip", ["id", "location_n", "circuit_id", "aep_circui", "aep_statio"], geometry=True)
    rec = _layer(src, "Recloser_Bank_Clip", ["id", "location_n", "circuit_id", "aep_circui", "aep_statio"], geometry=True)
    xf = _layer(src, "Transformer_Clip", ["id", "location_n", "circuit_id", "aep_circui", "aep_statio"], geometry=True)
    names = pd.concat([fuse, rec, xf])[["circuit_id", "aep_circui", "aep_statio"]].drop_duplicates()
    circuit_name = names.groupby("circuit_id").aep_circui.agg(lambda s: s.mode().iloc[0]).to_dict()
    station = names.groupby("aep_circui").aep_statio.agg(lambda s: s.mode().iloc[0]).to_dict()

    paths, dev_type, dev_loc, dev_circuit = {}, {}, {}, {}
    parents = defaultdict(Counter)
    skipped = 0
    for line in (src / "extracted").glob("*/matched_upstream.txt").__next__().read_text().splitlines():
        parts = line.split("\t")
        chain = [p.split("*") for p in parts[1:] if p]
        if not chain or chain[-1][2] != "Circuit Breaker" or chain[-1][3] not in circuit_name:
            skipped += 1
            continue
        cid = circuit_name[chain[-1][3]]
        ids = []
        for _, oid, typ, loc in reversed(chain):  # breaker first
            t = TOPO_TYPES[typ]
            did = ID_PREFIX[t] + oid
            dev_type[did], dev_loc[did] = t, loc
            dev_circuit.setdefault(did, cid)
            ids.append(did)
        sub = station.get(cid, "STATION?")
        full = [sub, *ids]
        for par, child in zip(full, full[1:]):
            parents[child][par] += 1
        paths["P" + parts[0]] = full
    devices = pd.DataFrame([{"device_id": d, "device_type": dev_type[d], "circuit_id": dev_circuit[d],
                             "parent_id": parents[d].most_common(1)[0][0]} for d in dev_type])
    # Coordinates for devices we place damage on (layers join by object id or location number).
    coords = {}
    for layer, t in ((xf, "XFMR"), (fuse, "FUSE"), (rec, "REC")):
        by_id = {ID_PREFIX[t] + str(r.id): (r.lat, r.lon) for r in layer.itertuples()}
        by_loc = {str(r.location_n): (r.lat, r.lon) for r in layer.itertuples()}
        for d in devices.loc[devices.device_type == t, "device_id"]:
            c = by_id.get(d) or by_loc.get(dev_loc[d])
            if c:
                coords[d] = c
    return paths, devices, coords, circuit_name, station, skipped


class Locator:
    """Nearest device of a type on a circuit (haversine BallTree per circuit+type)."""

    def __init__(self, devices: pd.DataFrame, coords: dict):
        self.trees = {}
        for (cid, t), g in devices[devices.device_id.isin(coords)].groupby(["circuit_id", "device_type"]):
            ids = g.device_id.tolist()
            pts = np.radians([coords[d] for d in ids])
            self.trees[(cid, t)] = (BallTree(pts, metric="haversine"), ids)

    def nearest(self, cid: str, t: str, lat: float, lon: float) -> tuple[str | None, float]:
        if (cid, t) not in self.trees:
            return None, float("inf")
        tree, ids = self.trees[(cid, t)]
        dist, idx = tree.query(np.radians([[lat, lon]]), k=1)
        return ids[idx[0][0]], float(dist[0][0] * 6_371_000)


def zone_device(devices_by_id: dict, device_id: str) -> str:
    """Walk up from a transformer to the first protective device (what a primary fault trips)."""
    d = device_id
    while d in devices_by_id and devices_by_id[d]["device_type"] not in PROTECTIVE:
        d = devices_by_id[d]["parent_id"]
    return d


# ---------- damage + storms ----------

def build_damage(src: Path, devices: pd.DataFrame, coords: dict, report: dict):
    cols = ["Substation", "Circuit", "Report_Tim", "Repaired", "Repaired_D", "Asset_Type", "Inform_Wor",
            "Access_Iss", "Special_Eq", "Longitude", "Latitude"]
    a = _layer(src, "arcos_prod_damage_09_28_Clip", cols)
    a["report_date"] = pd.to_datetime(a.Report_Tim).dt.normalize()
    a["repair_date"] = pd.to_datetime(a.Repaired_D).dt.normalize()
    a = a.sort_values("report_date").reset_index(drop=True)
    a["cluster"] = (a.report_date.diff().dt.days > STORM_GAP_DAYS).cumsum()
    sizes = a.groupby("cluster").size()
    storm_clusters = sizes[sizes >= MIN_STORM_RECORDS].index
    report["records_total"] = len(a)
    report["dropped_not_a_storm"] = int((~a.cluster.isin(storm_clusters)).sum())

    storms = []
    for k, cl in enumerate(storm_clusters, start=1):
        g = a.loc[a.cluster == cl]
        start = g.report_date.min()
        storms.append({"event_id": f"E{k:02d}", "cluster": cl, "name": f"{start:%b %Y} storm",
                       "start_time": start, "damage_records": len(g)})
    storms = pd.DataFrame(storms)
    # No weather data in the package: wind is assumed, scaled from how much damage the event caused
    # (log scale, 35 mph for the smallest storm to 75 mph for the largest). Replace with NOAA data.
    lo, hi = np.log(storms.damage_records.min()), np.log(storms.damage_records.max())
    storms["max_wind_mph"] = (35 + 40 * (np.log(storms.damage_records) - lo) / max(hi - lo, 1e-9)).round().astype(int)
    storms["ice_in"] = 0.0
    storms["weather_source"] = "assumed from damage volume"

    a = a.loc[a.cluster.isin(storm_clusters)].merge(storms[["cluster", "event_id", "start_time"]], on="cluster")
    by_id = devices.set_index("device_id").to_dict("index")
    loc = Locator(devices, coords)
    rows, drops = [], Counter()
    for r in a.itertuples():
        if r.Repaired != "Yes" or pd.isna(r.repair_date):
            drops["not_repaired"] += 1
            continue
        if (r.repair_date - r.report_date).days > MAX_REPAIR_DAYS:
            drops["repair_after_7_days"] += 1
            continue
        asset = r.Asset_Type if r.Asset_Type in ASSET_DAMAGE else INFORM_FALLBACK.get(r.Inform_Wor)
        dtype, where = ASSET_DAMAGE[asset]
        if where.startswith("self:"):
            dev, dist = loc.nearest(r.Circuit, where[5:], r.Latitude, r.Longitude)
        else:
            dev, dist = loc.nearest(r.Circuit, "XFMR", r.Latitude, r.Longitude)
            if dev and where == "zone":
                dev = zone_device(by_id, dev)
        if dev is None:
            drops["circuit_not_in_topology"] += 1
            continue
        extra = ACCESS_ADDER if r.Access_Iss == "Yes" else 0.0
        if isinstance(r.Special_Eq, str) and r.Special_Eq:
            extra += EQUIPMENT_ADDER.get(r.Special_Eq, DEFAULT_EQUIPMENT_ADDER)
        report_t = r.report_date + pd.Timedelta(hours=REPORT_HOUR)
        repair_t = max(r.repair_date + pd.Timedelta(hours=REPAIR_HOUR), report_t + pd.Timedelta(hours=1))
        rows.append({"event_id": r.event_id, "region": "APCO", "district": None, "substation_id": r.Substation,
                     "circuit_id": r.Circuit, "device_id": dev, "asset_type": asset, "damage_type": dtype,
                     "quantity": 1, "crew_count": None, "report_time": report_t.isoformat(timespec="minutes"),
                     "repair_complete_time": repair_t.isoformat(timespec="minutes"), "repair_status": "complete",
                     "extra_hours": extra, "placement_m": round(dist)})
    damage = pd.DataFrame(rows)
    damage.insert(0, "incident_id", [f"INC{i:05d}" for i in range(1, len(damage) + 1)])
    report["dropped"] = dict(drops)
    report["records_used"] = len(damage)
    report["placement_m_median"] = float(damage.placement_m.median())
    storms["start_time"] = storms.start_time.dt.strftime("%Y-%m-%dT00:00")
    return damage, storms.drop(columns="cluster")


def observed_crews(damage: pd.DataFrame, hours: dict, indirect: dict) -> pd.Series:
    """ARCOS has no crew counts. Infer them from throughput: the crew-hours of work a circuit
    had in a storm, divided by how long its repairs actually took (at least 12 h)."""
    mult = float(np.prod(list(indirect.values())))
    work = (damage.damage_type.map(hours) * damage.quantity + damage.extra_hours) * mult
    g = damage.assign(work=work, rep=pd.to_datetime(damage.report_time), fix=pd.to_datetime(damage.repair_complete_time))
    g = g.groupby(["event_id", "circuit_id"]).agg(work=("work", "sum"), start=("rep", "min"), end=("fix", "max"))
    span = ((g.end - g.start).dt.total_seconds() / 3600).clip(lower=12)
    crews = (g.work / span).round().clip(lower=1).astype(int)
    return damage.set_index(["event_id", "circuit_id"]).index.map(crews.to_dict())


# ---------- circuits, premises, facilities ----------

def build_circuits(src: Path, circuits: set, station: dict) -> pd.DataFrame:
    pri = _layer(src, "Primary_ExportFeatures", ["aep_circui", "mounting", "report_len"])
    pri = pri.loc[pri.aep_circui.isin(circuits)]
    length = pri.groupby("aep_circui").report_len.sum()
    oh = pri.loc[pri.mounting == "Overhead"].groupby("aep_circui").report_len.sum()
    out = pd.DataFrame({"circuit_id": sorted(circuits)})
    out["substation_id"] = out.circuit_id.map(station)
    out["region"] = "APCO"
    out["district"] = None
    out["overhead_pct"] = (out.circuit_id.map(oh).fillna(0) / out.circuit_id.map(length)).fillna(0.5).round(2)
    out["length_mi"] = (out.circuit_id.map(length).fillna(0) / 5280).round(1)  # report_len is in feet
    return out


def build_premises(src: Path, paths: dict, circuit_name: dict, circuit_n: dict) -> pd.DataFrame:
    # Only the premise number, circuit and service point are read: no names, addresses or flags.
    cust = _layer(src, "Customer_Clip", ["premise_nu", "circuit_id", "service_po", "service__1"])
    cust["premise_id"] = "P" + cust.premise_nu.astype(str)
    cust["circuit_id"] = cust.circuit_id.map(circuit_name)
    cust = cust.loc[cust.premise_id.isin(paths) & cust.circuit_id.isin(circuit_n)].drop_duplicates("premise_id")
    lon, lat = cust.service_po.astype(float), cust.service__1.astype(float)
    med = pd.DataFrame({"c": cust.circuit_id, "lat": lat, "lon": lon}).groupby("c").median()
    r = np.where(lat >= cust.circuit_id.map(med.lat), 1, 2)
    f = np.where(lon < cust.circuit_id.map(med.lon), 1, 2)
    # Four coarse areas per circuit (north/south x west/east of its median): enough for a map, too coarse to locate a home.
    cust["hex_id"] = [f"hex-{circuit_n[c]:02d}-{a}{b}" for c, a, b in zip(cust.circuit_id, r, f)]
    return cust[["premise_id", "circuit_id", "hex_id"]]


def build_priority(src: Path, devices: pd.DataFrame, coords: dict) -> pd.DataFrame:
    xf = devices.loc[(devices.device_type == "XFMR") & devices.device_id.isin(coords)]
    ids = xf.device_id.tolist()
    tree = BallTree(np.radians([coords[d] for d in ids]), metric="haversine")
    rows = []
    for name, ftype in (("Hospitals_Medic_Pairwis_Clip", "hospital"), ("Fire_Stations_E_Pairwis_Clip", "fire_department"),
                        ("Ambulance_Servi_Pairwis_Clip", "ambulance")):
        fac = _layer(src, name, ["FCODE"], geometry=True)
        dist, idx = tree.query(np.radians(fac[["lat", "lon"]].to_numpy()), k=1)
        for dm, i in zip(dist[:, 0] * 6_371_000, idx[:, 0]):
            if dm <= FACILITY_MAX_M:
                d = ids[i]
                rows.append({"facility_type": ftype, "circuit_id": xf.loc[xf.device_id == d, "circuit_id"].iloc[0], "device_id": d})
    out = pd.DataFrame(rows)
    out.insert(0, "facility_id", [f"F{i:03d}" for i in range(1, len(out) + 1)])
    return out


# ---------- personas ----------

def pick_personas(paths, premises, damage, storms, replay_event, priority):
    """Synthetic people on real (anonymized) premises, chosen by rule so the demo is reproducible."""
    start = pd.Timestamp(storms.loc[storms.event_id == replay_event, "start_time"].iloc[0])
    ev = damage.loc[damage.event_id == replay_event].copy()
    ev["done_h"] = (pd.to_datetime(ev.repair_complete_time) - start).dt.total_seconds() / 3600
    hist = damage.groupby("circuit_id").event_id.nunique()
    prio_circuits = set(priority.circuit_id)
    by_dev = ev.groupby("device_id")
    prem = premises.set_index("premise_id")

    def score(p):  # Maria: several repairs on her path, restored in 1-3 days, plenty of history
        path = paths[p]
        on = [d for d in path if d in by_dev.groups]
        if len(on) < 2:
            return None
        back = max(by_dev.get_group(d).done_h.max() for d in on)
        cid = prem.circuit_id[p]
        if not (24 <= back <= 72) or hist.get(cid, 0) < 3:
            return None
        return (-(cid in prio_circuits), -len(on), -hist[cid], p)

    maria = min((s for p in sorted(prem.index) if (s := score(p))), default=(None,) * 4)[-1]
    latest = damage.merge(storms[["event_id", "start_time"]]).groupby("circuit_id").start_time.min()
    new_circuits = set(premises.circuit_id) - set(hist.index)
    dev = next((p for p in sorted(prem.index) if prem.circuit_id[p] in new_circuits), None)
    if dev is None:  # every circuit has damage: take the one whose history starts most recently
        dev = prem.index[prem.circuit_id == latest.idxmax()][0]
    damaged = set(damage.device_id)
    sam = next(p for p in sorted(prem.index) if hist.get(prem.circuit_id[p], 0) >= 3
               and not damaged & set(paths[p]))
    return [
        {"phone": "+15550100001", "name": "Maria", "premise_id": maria, "alerts_opt_in": True,
         "declared_needs": [{"need": "CPAP", "battery_hours": 8}], "language": "en"},
        {"phone": "+15550100002", "name": "Dev", "premise_id": dev, "alerts_opt_in": True,
         "declared_needs": [], "language": "en"},
        {"phone": "+15550100003", "name": "Sam", "premise_id": sam, "alerts_opt_in": False,
         "declared_needs": [{"need": "insulin_fridge", "battery_hours": 4}], "language": "en"},
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", type=Path, default=SRC, help="the package's data/ folder (with extracted/)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    src, out = args.src, args.out
    if not (src / "extracted").is_dir():
        raise SystemExit(f"{src / 'extracted'} not found: extract the archives first (see {src / 'README.md'})")
    out.mkdir(parents=True, exist_ok=True)
    report = {"assumptions": {
        "storm_events": f"ARCOS has no event ids; reports more than {STORM_GAP_DAYS} days apart start a new event, "
                        f"and events with fewer than {MIN_STORM_RECORDS} records are dropped as everyday damage",
        "times": f"ARCOS has dates only; reports are placed at {REPORT_HOUR:02d}:00 and repairs at {REPAIR_HOUR:02d}:00",
        "repairs": f"open records and repairs logged more than {MAX_REPAIR_DAYS} days after the report are excluded "
                   "(follow-up work, not storm restoration)",
        "weather": "no weather data; max_wind_mph is assumed from each event's damage volume (35-75 mph, log scale)",
        "crews": "no crew data; crew_count per storm and circuit is inferred from throughput "
                 "(crew-hours of work / hours its repairs took, min 12 h)",
        "labor": f"workbook person-hours per repair recipe (labor_basis.csv) x indirect labor multiplier, "
                 f"worked by a {CREW_SIZE}-person crew",
        "placement": "damage is placed at the nearest device on its circuit; wire/pole/switch damage at the "
                     "protective device above the nearest transformer",
        "facilities": f"public facility points within {FACILITY_MAX_M} m of a transformer; only device and circuit kept",
        "personas": "synthetic people on real anonymized premises, picked by rule (realdata.pick_personas)",
    }}

    hours, basis, indirect = labor_tables(src / "Install_Labor_HackOHIO.xlsx")
    paths, devices, coords, circuit_name, station, skipped = load_network(src)
    report["topology_lines_skipped"] = skipped
    circuits = build_circuits(src, set(devices.circuit_id), station)
    circuit_n = {c: i for i, c in enumerate(circuits.circuit_id, start=1)}
    premises = build_premises(src, paths, circuit_name, circuit_n)
    paths = {p: paths[p] for p in premises.premise_id}
    damage, storms = build_damage(src, devices, coords, report)
    damage["crew_count"] = observed_crews(damage, hours, indirect)
    priority = build_priority(src, devices, coords)
    replay_event = storms.sort_values("damage_records").event_id.iloc[-1]
    customers = pick_personas(paths, premises, damage, storms, replay_event, priority)

    circuits.to_csv(out / "circuits.csv", index=False)
    devices.to_csv(out / "devices.csv", index=False)
    premises.to_csv(out / "premises.csv", index=False)
    (out / "topology.txt").write_text("# premise_id|upline devices, substation first\n"
                                      + "\n".join(f"{p}|{'>'.join(path)}" for p, path in paths.items()) + "\n")
    storms.to_csv(out / "storms.csv", index=False)
    damage.to_csv(out / "arcos_damage.csv", index=False)
    pd.DataFrame(hours.items(), columns=["damage_type", "labor_hours_per_unit"]).to_csv(out / "labor_hours.csv", index=False)
    basis.to_csv(out / "labor_basis.csv", index=False)
    pd.DataFrame(indirect.items(), columns=["factor", "multiplier"]).to_csv(out / "indirect_labor.csv", index=False)
    priority.to_csv(out / "priority_locations.csv", index=False)
    (out / "customers.json").write_text(json.dumps(customers, indent=2))
    big = storms.loc[storms.event_id == replay_event].iloc[0]
    (out / "current_storm.json").write_text(json.dumps({
        "name": "Storm Kestrel", "eta_hours": 18, "max_wind_mph": int(big.max_wind_mph), "ice_in": 0.0,
        "replay_of_event": replay_event,
        "note": f"Forecast for the demo. Once the outage starts we replay the real {big['name']} ({replay_event}).",
    }, indent=2))
    report.update({"circuits": len(circuits), "devices": len(devices), "premises": len(premises),
                   "storms": storms[["event_id", "name", "damage_records", "max_wind_mph"]].to_dict("records"),
                   "facilities": len(priority), "replay_event": replay_event,
                   "personas": {c["name"]: c["premise_id"] for c in customers}})
    (out / "conversion_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k: report[k] for k in ("records_total", "records_used", "dropped", "dropped_not_a_storm",
                                             "circuits", "premises", "facilities", "replay_event", "personas")}, indent=2))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
