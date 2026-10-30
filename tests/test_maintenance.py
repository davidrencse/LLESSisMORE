from cryptography.fernet import Fernet
import pytest

from lifeev.maintenance import candidates, prune, restore_snapshot
from lifeev.storage import Store


def test_candidates_rank_low_usefulness_first_and_pins_raise_score(tmp_path, make_store):
    store = make_store(tmp_path / "db.sqlite")
    old = store.create({"name": "old"})
    young = store.create({"name": "young"})
    # Give the first node a dependent and a revision so both usefulness signals count.
    child = store.create({"name": "child"}, old["id"])
    store.revise(old["id"], {"name": "updated"})

    ranked = candidates(store)
    scores = {item["id"]: item["score"] for item in ranked}
    assert scores[old["id"]] > scores[young["id"]]
    assert scores[old["id"]] >= scores[child["id"]]
    pinned = {item["id"]: item["score"] for item in candidates(store, pins={young["id"]})}
    assert pinned[young["id"]] > scores[old["id"]]
    assert "pinned" in next(item["reason"] for item in candidates(store, {young["id"]}) if item["id"] == young["id"])


def test_prune_archives_only_selected_and_refuses_pins(tmp_path, make_store):
    store = make_store(tmp_path / "db.sqlite")
    first = store.create({"name": "first"})
    second = store.create({"name": "second"})
    with pytest.raises(ValueError, match="pinned"):
        prune(store, [first["id"]], pins={first["id"]})
    assert store.get(first["id"])["archived_at"] is None

    snapshot_dir = store.path.parent / (store.path.name + ".snapshots")
    before = len(list(snapshot_dir.glob("*.fernet")))
    archived = prune(store, [first["id"]])
    assert len(archived) == 1 and archived[0]["id"] == first["id"]
    assert store.get(first["id"])["archived_at"]
    assert store.get(second["id"])["archived_at"] is None
    assert len(list(snapshot_dir.glob("*.fernet"))) > before


def test_restore_snapshot_recovers_store_and_never_overwrites(tmp_path, make_store):
    key = make_store.key
    source = make_store(tmp_path / "source.sqlite")
    original = source.create({"secret": "recover me"})
    snapshot = source.snapshot("manual")
    destination = tmp_path / "recovered.sqlite"

    recovered = restore_snapshot(snapshot, destination, key)
    assert recovered.get(original["id"])["document"] == {"secret": "recover me"}
    assert recovered.path == destination
    with pytest.raises(FileExistsError):
        restore_snapshot(snapshot, destination, key)


def test_restore_snapshot_rejects_wrong_key_without_creating_destination(tmp_path, make_store):
    store = make_store(tmp_path / "source.sqlite")
    node = store.create({"secret": "still private"})
    snapshot = store.snapshot("manual")
    destination = tmp_path / "bad.sqlite"
    with pytest.raises(ValueError, match="decrypt"):
        restore_snapshot(snapshot, destination, Fernet.generate_key())
    assert not destination.exists()
    assert node["id"]
