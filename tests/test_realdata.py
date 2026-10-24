"""Checks on the converted AEP extracts (data/real/). Skipped until
`python -m concierge.realdata` has been run."""
import pytest

from concierge.config import REAL_DIR
from concierge.data import get_dataset

pytestmark = pytest.mark.skipif(not (REAL_DIR / "devices.csv").exists(), reason="data/real/ not built")


@pytest.fixture(scope="module")
def ds():
    return get_dataset(str(REAL_DIR))


def test_every_damage_record_sits_on_the_network(ds):
    assert len(ds.damage) > 1000
    assert ds.damage.device_id.isin(ds.graph.nodes).all()
    assert set(ds.damage.event_id) <= set(ds.storms.event_id)
    assert set(ds.damage.damage_type) <= set(ds.labor_hours)
    assert (ds.damage.repair_complete_time > ds.damage.report_time).all()


def test_paths_run_substation_to_transformer(ds):
    for c in ds.customers:
        path = ds.path(c["premise_id"])
        assert path and ds.device_type(path[0]) == "SUB" and ds.device_type(path[1]) == "BRK"
        assert ds.device_type(path[-1]) == "XFMR"


def test_no_sensitive_fields_in_tables(ds):
    # Only IDs, circuits and coarse map areas: no names, addresses, coordinates or the life-support flag.
    assert list(ds.premises.columns) == ["premise_id", "circuit_id", "hex_id"]
    assert list(ds.priority.columns) == ["facility_id", "facility_type", "circuit_id", "device_id"]
    assert not {"Longitude", "Latitude", "lat", "lon"} & set(ds.damage.columns)


def test_personas_cover_the_demo_cases(ds):
    from concierge.risk import predict
    maria, dev, sam = ds.customers
    assert predict(maria["premise_id"], ds=ds)["status"] == "ok"
    assert predict(dev["premise_id"], ds=ds)["status"] == "insufficient_data"
    assert predict(sam["premise_id"], ds=ds)["status"] == "ok"


def test_maria_is_out_in_the_replayed_storm(ds):
    from concierge.replay import actual_restore_h, event_records
    maria = ds.customers[0]
    recs = event_records(ds, ds.current_storm["replay_of_event"], ds.circuit_of(maria["premise_id"]))
    back = actual_restore_h(ds, maria["premise_id"], recs)
    assert back is not None and 24 <= back <= 72
