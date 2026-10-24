"""The operations console: staff fixes must reach the customer side, and only the right customers."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from concierge import ops, portal, service
from concierge.api import app
from concierge.data import get_dataset

DS = get_dataset()
MARIA = DS.customers[0]
CID = DS.circuit_of(MARIA["premise_id"])
OUT_H = 7.0
client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh():
    service.reset_world()
    service.set_clock(OUT_H)
    yield
    service.reset_world()


def me():
    return client.get(f"/api/me/{MARIA['premise_id']}").json()


def act(kind, payload, circuit=CID):
    r = client.post("/api/ops/actions", json={"kind": kind, "circuit_id": circuit, "payload": payload})
    assert r.status_code == 200, r.text
    return r.json()["action"]


def test_customer_sees_outage_and_estimate():
    s = me()
    assert s["state"] == "out" and s["estimate"]["window"] and s["outage_since"]


def test_more_crews_brings_the_estimate_in():
    def p90():
        est, _ = service.restoration_for(MARIA, service.WORLD)
        return est["hours_from_now"]["p90"]
    before = p90()
    act("crews", {"crews": 8})
    assert p90() < before


def test_confirmed_time_reaches_status_card_and_chatbot_tools():
    act("etr_confirmed", {"at_h": OUT_H + 10})
    s = me()
    assert s["estimate"]["confirmed_etr"]
    card = service.make_card("status", MARIA, service.WORLD)
    assert any("confirm" in line for line in card["confirmed"]) and card["restoration"]["confirmed_etr"]


def test_notice_scope():
    act("notice", {"text": "Warming center open at the library", "level": "info"}, circuit=None)
    act("notice", {"text": "Crews are on your street", "level": "info"})
    other = next(c for c in DS.circuits.circuit_id if c != CID)
    act("notice", {"text": "Only for another circuit", "level": "warning"}, circuit=other)
    texts = [n["text"] for n in me()["notices"]]
    assert "Warming center open at the library" in texts and "Crews are on your street" in texts
    assert "Only for another circuit" not in texts


def test_repairing_everything_on_the_path_restores_power_and_undo_reverts():
    open_ids = [r["incident_id"] for r in portal.circuit_detail(CID)["open"]]
    path = set(DS.path(MARIA["premise_id"]))
    mine = [r["incident_id"] for r in portal.circuit_detail(CID)["open"] if r["device_id"] in path]
    assert mine and set(mine) <= set(open_ids)
    acts = [act("repair_done", {"incident_id": i}) for i in mine]
    assert me()["state"] == "restored"
    client.post(f"/api/ops/actions/{acts[-1]['id']}/undo")
    assert me()["state"] == "out"


def test_logged_damage_keeps_a_customer_out_until_repaired():
    path = set(DS.path(MARIA["premise_id"]))
    for r in portal.circuit_detail(CID)["open"]:
        if r["device_id"] in path:
            act("repair_done", {"incident_id": r["incident_id"]})
    assert me()["state"] == "restored"
    dev = next(d for d in DS.path(MARIA["premise_id"]) if DS.device_type(d) in ("FUSE", "REC", "BRK"))
    a = act("damage_found", {"device_id": dev, "damage_type": "pole_replace", "quantity": 1})
    assert me()["state"] == "out"
    act("repair_done", {"incident_id": f"OPS{a['id']:04d}"})
    assert me()["state"] == "restored"


@pytest.mark.parametrize("kind,payload,circuit", [
    ("crews", {"crews": 99}, CID),
    ("repair_done", {"incident_id": "INC99999"}, CID),
    ("damage_found", {"device_id": "nope", "damage_type": "pole_replace"}, CID),
    ("etr_confirmed", {"at_h": 0}, CID),
    ("notice", {"text": "   "}, None),
    ("crews", {"crews": 3}, "NOT_A_CIRCUIT"),
    ("delete_everything", {}, CID),
])
def test_bad_actions_are_refused(kind, payload, circuit):
    r = client.post("/api/ops/actions", json={"kind": kind, "circuit_id": circuit, "payload": payload})
    assert r.status_code == 400
    assert not ops.actions()


def test_actions_apply_from_the_hour_they_were_entered():
    act("crews", {"crews": 8})
    assert ops.crews_for(CID, OUT_H) == 8 and ops.crews_for(CID, OUT_H - 1) is None


def test_overview_counts_and_console_endpoints():
    ov = client.get("/api/ops/overview").json()
    assert ov["customers_out"] > 0 and ov["open_repairs"] > 0 and any(r["circuit_id"] == CID for r in ov["circuits"])
    d = client.get(f"/api/ops/circuits/{CID}").json()
    assert d["open"] and d["devices"] and d["etr_options"]
    assert client.get("/api/ops/circuits/NOPE").status_code == 404
    assert any(e["who"] == MARIA["name"] for e in client.get("/api/ops/escalations").json())  # CPAP check-in
    act("crews", {"crews": 5})
    assert client.get("/api/ops/actions").json()[0]["what"] == "Set crews"


def test_customer_api_leaks_no_internal_ids():
    act("etr_confirmed", {"at_h": OUT_H + 5})
    text = json.dumps(me())
    assert not re.search(r"\bC\d{2}-[A-Z]|\bINC\d+|\bOPS\d+|\bP\d{5,}\b", text.replace(MARIA["premise_id"], ""))


def test_reset_clears_staff_actions_and_clock():
    act("crews", {"crews": 5})
    client.post("/api/ops/reset")
    assert not ops.actions() and client.get("/api/state").json()["phase"] == "pre_storm"


def test_pages_are_served():
    assert "Outage Center" in client.get("/").text
    assert "Storm Operations" in client.get("/ops").text
    assert client.get("/static/app.css").status_code == 200
