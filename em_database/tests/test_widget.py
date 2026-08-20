"""Tests for the dataset catalogue and the anywidget browser.

The catalogue tests need no network (they only instantiate the dataset classes
and read declared metadata). The widget tests need ``anywidget``; the one real
download is marked ``slow``.
"""
import threading

import pytest

import em_database
from em_database import catalogue

TINY_DATASET = "CuZnHAADF"  # 34 kB - the smallest file in the index


# ---------------------------------------------------------------------------
# catalogue
# ---------------------------------------------------------------------------

def test_catalogue_groups_and_orders_by_technique():
    cat = catalogue.catalogue()
    assert cat["n_total"] > 0
    assert cat["groups"], "expected at least one technique group"
    techniques = [g["technique"] for g in cat["groups"]]
    # Known techniques appear in the declared order, ahead of any extras.
    known = [t for t in techniques if t in catalogue.TECHNIQUE_ORDER]
    expected = [t for t in catalogue.TECHNIQUE_ORDER if t in known]
    assert known == expected
    # n_total is the sum of the group sizes.
    assert cat["n_total"] == sum(len(g["items"]) for g in cat["groups"])


def test_catalogue_entry_has_expected_fields():
    ds = catalogue.resolve(TINY_DATASET)
    row = catalogue.entry(TINY_DATASET, ds)
    for key in ("name", "technique", "size", "downloaded", "path", "description",
                "detector", "microscope", "voltage", "tags", "source", "file"):
        assert key in row
    assert row["name"] == TINY_DATASET
    assert row["technique"] == "STEM"
    assert isinstance(row["downloaded"], bool)


def test_catalogue_downloaded_flag_tracks_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("EM_DATABASE_DATA_DIR", str(tmp_path))
    ds = catalogue.resolve(TINY_DATASET)
    assert catalogue.entry(TINY_DATASET, ds)["downloaded"] is False
    (tmp_path / ds.file).write_bytes(b"x")  # pretend it is downloaded
    assert catalogue.entry(TINY_DATASET, ds)["downloaded"] is True


# ---------------------------------------------------------------------------
# widget
# ---------------------------------------------------------------------------

def _browser():
    pytest.importorskip("anywidget")
    return em_database.browse()


def test_browse_returns_widget_populated_from_the_catalogue():
    widget = _browser()
    assert widget.n_total > 0
    assert widget.groups
    assert widget.n_total == sum(len(g["items"]) for g in widget.groups)
    assert isinstance(widget.data_dir, str) and widget.data_dir


def test_progress_trait_plumbing():
    widget = _browser()
    widget._set_progress("tok", "Foo", 30, 100)
    assert widget.downloads["tok"] == {"label": "Foo", "done": 30, "total": 100}
    widget._set_error("tok", "Foo", "boom")
    assert widget.downloads["tok"]["error"] == "boom"
    widget._clear_progress("tok")
    assert "tok" not in widget.downloads


def test_command_trait_routes_to_actions(monkeypatch):
    """Setting the `_command` trait (what the frontend does) dispatches."""
    widget = _browser()
    calls = []
    monkeypatch.setattr(widget, "_start_download", lambda name: calls.append(("dl", name)))
    monkeypatch.setattr(widget, "_cancel", lambda token: calls.append(("cancel", token)))
    monkeypatch.setattr(widget, "_delete", lambda name: calls.append(("del", name)))
    widget._command = {"action": "download", "name": "X", "nonce": 1}
    widget._command = {"action": "cancel", "token": "t", "nonce": 2}
    widget._command = {"action": "delete", "name": "Y", "nonce": 3}
    assert calls == [("dl", "X"), ("cancel", "t"), ("del", "Y")]


def test_widget_does_not_shadow_ipywidgets_comm_handler():
    """`_handle_msg` is ipywidgets' internal comm callback - overriding it breaks
    all comm handling (trait sync included). The widget must not define one, so
    the inherited handler stays intact."""
    widget = _browser()
    assert "_handle_msg" not in type(widget).__dict__
    # It resolves to ipywidgets' Widget, not our subclass.
    assert type(widget)._handle_msg.__qualname__.split(".")[0] != type(widget).__name__


def test_command_update_routes_through_real_comm_handler(monkeypatch):
    """A _command state-update (what save_changes sends) dispatches without the
    TypeError that a shadowed _handle_msg used to raise."""
    widget = _browser()
    got = []
    monkeypatch.setattr(widget, "_start_download", lambda name: got.append(name))
    msg = {"content": {"data": {"method": "update",
            "state": {"_command": {"action": "download", "name": "X", "nonce": 1}}}},
           "buffers": []}
    widget._handle_msg(msg)  # the real ipywidgets handler
    assert got == ["X"]


def test_search_blob_includes_authors_and_affiliation():
    ds = catalogue.resolve("BilayerWS2")
    row = catalogue.entry("BilayerWS2", ds)
    assert "nick hagopian" in row["search"]      # author name
    assert "wisconsin" in row["search"]          # author affiliation
    assert "4d-stem" in row["search"]            # technique


def test_delete_removes_downloaded_file(tmp_path, monkeypatch):
    monkeypatch.setenv("EM_DATABASE_DATA_DIR", str(tmp_path))
    ds = catalogue.resolve(TINY_DATASET)
    (tmp_path / ds.file).write_bytes(b"x")
    assert ds.filepath() is not None
    assert ds.delete() is True
    assert ds.filepath() is None
    assert ds.delete() is False  # nothing left to delete


def test_cancel_sets_the_event():
    widget = _browser()
    event = threading.Event()
    widget._cancels["tok"] = event
    widget._cancel("tok")
    assert event.is_set()


def test_widget_quiets_pooch_download_logs():
    """Creating a widget silences pooch's noisy "Downloading data from" INFO
    logs (they render as red output in Jupyter)."""
    pytest.importorskip("anywidget")
    import logging

    import pooch

    import em_database.widget as widget_mod

    widget_mod._pooch_quieted = False
    pooch.get_logger().setLevel(logging.INFO)
    em_database.browse()  # should quiet pooch as a side effect
    assert pooch.get_logger().level >= logging.WARNING


def test_dataset_card_is_populated_and_routes(monkeypatch):
    pytest.importorskip("anywidget")
    from em_database.widget import card

    ds = catalogue.resolve(TINY_DATASET)
    widget = card(ds)
    assert widget.info["name"] == TINY_DATASET
    assert widget.info["technique"] == "STEM"
    calls = []
    monkeypatch.setattr(widget, "_start_download", lambda: calls.append("dl"))
    widget._command = {"action": "download", "nonce": 1}
    assert calls == ["dl"]


def test_dataset_display_is_a_widget_card():
    pytest.importorskip("anywidget")
    ds = catalogue.resolve(TINY_DATASET)
    bundle = ds._repr_mimebundle_()
    mimes = bundle[0] if isinstance(bundle, tuple) else bundle
    assert "application/vnd.jupyter.widget-view+json" in mimes


def test_dataset_display_falls_back_without_anywidget(monkeypatch):
    import em_database.widget as widget_mod

    def _boom(_dataset):
        raise ImportError("no anywidget")

    monkeypatch.setattr(widget_mod, "card", _boom)
    ds = catalogue.resolve(TINY_DATASET)
    bundle = ds._repr_mimebundle_()
    assert "text/plain" in bundle


@pytest.mark.slow
def test_widget_download_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("EM_DATABASE_DATA_DIR", str(tmp_path))
    widget = _browser()
    future = widget._start_download(TINY_DATASET)
    assert future is not None
    future.result(timeout=120)  # block until the background download finishes
    ds = catalogue.resolve(TINY_DATASET)
    assert (tmp_path / ds.file).exists()
