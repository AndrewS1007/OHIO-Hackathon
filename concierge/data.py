"""Load the challenge tables and build the topology graph.

Everything downstream reads from `get_dataset()`. To switch to the real
extracts, convert them into the same tables and point CONCIERGE_DATA_DIR at them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property, lru_cache
from pathlib import Path

import networkx as nx
import pandas as pd

from .config import DATA_DIR

# SECT (sectionalizer) and STEP (step transformer) only appear in the real topology.
DEPTH = {"SUB": 0, "BRK": 1, "REC": 2, "SECT": 2, "STEP": 2, "FUSE": 3, "XFMR": 4}
DEVICE_WORDS = {"BRK": "main line", "REC": "recloser", "SECT": "sectionalizer", "STEP": "step transformer",
                "FUSE": "fuse", "XFMR": "transformer"}


@dataclass(eq=False)  # hash by identity so engines can cache per dataset
class Dataset:
    circuits: pd.DataFrame
    devices: pd.DataFrame
    premises: pd.DataFrame
    storms: pd.DataFrame
    damage: pd.DataFrame
    labor_hours: dict[str, float]
    indirect: dict[str, float]
    priority: pd.DataFrame
    customers: list[dict]
    current_storm: dict
    graph: nx.DiGraph  # edges point downstream: parent -> child
    paths: dict[str, list[str]]  # premise -> upline devices (substation first)

    def path(self, premise_id: str) -> list[str] | None:
        return self.paths.get(premise_id)

    @cached_property
    def _circuit_by_premise(self) -> dict[str, str]:
        return dict(zip(self.premises.premise_id, self.premises.circuit_id))

    def circuit_of(self, premise_id: str) -> str | None:
        return self._circuit_by_premise.get(premise_id)

    def device_type(self, device_id: str) -> str:
        return self.graph.nodes[device_id].get("device_type", "SUB")

    def downstream_premises(self, device_id: str) -> set[str]:
        return {p for p, path in self.paths.items() if device_id in path}

    def priority_devices(self) -> set[str]:
        """Devices whose zone feeds a critical facility (used for crew sequencing)."""
        out = set()
        for dev in self.priority.device_id:
            if dev in self.graph:
                out |= set(nx.ancestors(self.graph, dev)) | {dev}
        return out

    def customer_by_phone(self, phone: str) -> dict | None:
        return next((c for c in self.customers if c["phone"] == phone), None)


def _read_topology(path: Path) -> dict[str, list[str]]:
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        premise, chain = line.split("|", 1)
        out[premise] = chain.split(">")
    return out


@lru_cache(maxsize=1)
def get_dataset(data_dir: str | None = None) -> Dataset:
    d = Path(data_dir) if data_dir else DATA_DIR
    devices = pd.read_csv(d / "devices.csv")
    g = nx.DiGraph()
    for r in devices.itertuples():
        g.add_node(r.device_id, device_type=r.device_type, circuit_id=r.circuit_id)
        if r.parent_id not in g:
            g.add_node(r.parent_id, device_type="SUB")
        g.add_edge(r.parent_id, r.device_id)
    damage = pd.read_csv(d / "arcos_damage.csv", parse_dates=["report_time", "repair_complete_time"])
    storms = pd.read_csv(d / "storms.csv", parse_dates=["start_time"])
    return Dataset(
        circuits=pd.read_csv(d / "circuits.csv"),
        devices=devices,
        premises=pd.read_csv(d / "premises.csv"),
        storms=storms,
        damage=damage,
        labor_hours=dict(pd.read_csv(d / "labor_hours.csv").values),
        indirect=dict(pd.read_csv(d / "indirect_labor.csv").values),
        priority=pd.read_csv(d / "priority_locations.csv"),
        customers=json.loads((d / "customers.json").read_text()),
        current_storm=json.loads((d / "current_storm.json").read_text()),
        graph=g,
        paths=_read_topology(d / "topology.txt"),
    )
