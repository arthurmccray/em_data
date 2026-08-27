"""Tests for downloading datasets.

The expensive part of testing a download index is not the download - it is
knowing that every ``source`` still resolves. Checking that costs a HEAD request
per dataset, so it is done for all of them. Actually pulling bytes only proves
that pooch and the checksum verification are wired up correctly, which is
identical for every entry, so it is done once with the smallest file in the
index. The large downloads are marked ``slow`` and deselected by default; run
them with ``pytest -m slow``.
"""

import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import em_database.data as data
from em_database.data import MgONanoCrystals, NiEBSDLarge
from em_database.downloadable_dataset import DownloadFuture

try:
    from quantem.core.io.file_readers import read_4dstem

    QUANTEM_AVAILABLE = True
except ImportError:
    QUANTEM_AVAILABLE = False

# The smallest file in the index (34 kB). Used wherever a test needs a real
# download to exercise pooch rather than to exercise a particular dataset.
TINY_DATASET = "CuZnHAADF"

ALL_DATASETS = sorted(data.__all__)


def _head(url, timeout=60):
    """Return the response for a HEAD request, following redirects."""
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": "em_database tests"}
    )
    return urllib.request.urlopen(request, timeout=timeout)


@pytest.mark.parametrize("name", ALL_DATASETS)
def test_source_url_resolves(name):
    """Every dataset's source URL must still exist.

    This is what actually breaks over time - a Zenodo record superseded, a
    GitHub ref rewritten - and it is invisible until someone tries to download.
    """
    dataset = getattr(data, name)()
    url = f"{dataset.source}/{dataset.file}"
    try:
        response = _head(url)
    except urllib.error.HTTPError as error:
        pytest.fail(f"{name}: {url} returned HTTP {error.code}")
    except urllib.error.URLError as error:  # pragma: no cover - transient
        pytest.skip(f"{name}: network unavailable ({error.reason})")
    assert response.status == 200, f"{name}: {url} returned {response.status}"


@pytest.mark.parametrize("name", ALL_DATASETS)
def test_metadata_is_complete(name):
    """Entries need enough metadata for pooch to fetch and verify them."""
    dataset = getattr(data, name)()
    assert dataset.source, f"{name} has no source"
    assert dataset.file, f"{name} has no file"
    assert dataset.description, f"{name} has no description"
    assert dataset.checksum and dataset.checksum.startswith("md5:"), (
        f"{name} has no md5 checksum, so a corrupt or truncated download would go unnoticed"
    )


def test_download_verifies_checksum(tmp_path):
    """A real download, to prove pooch and checksum verification are wired up."""
    dataset = getattr(data, TINY_DATASET)()
    path = dataset.download(destination=tmp_path, progressbar=False, background=False)
    assert (tmp_path / dataset.file).exists()
    assert isinstance(path, str)
    assert path == str(tmp_path / dataset.file)


def test_download_default_returns_path_handle(tmp_path):
    """The default download runs in the background and hands back a path handle
    that is a real ``Path`` and resolves to the downloaded file."""
    dataset = getattr(data, TINY_DATASET)()
    handle = dataset.download(destination=tmp_path, progressbar=False)
    assert isinstance(handle, DownloadFuture)
    assert isinstance(handle, Path)
    # Using it as a path blocks until the bytes are there, then behaves normally.
    assert os.fspath(handle) == str(tmp_path / dataset.file)
    assert handle.is_file()
    assert (tmp_path / dataset.file).exists()
    assert handle.done()


def test_download_handle_is_nonblocking_then_blocks_on_use(tmp_path, monkeypatch):
    """download() returns before the file exists; touching the path waits for it."""
    dataset = getattr(data, TINY_DATASET)()
    started = threading.Event()

    def slow_retrieve(destination=None, progressbar=True, chunk_size=4096):
        started.set()
        time.sleep(0.4)
        target = tmp_path / dataset.file
        target.write_bytes(b"payload")
        return str(target)

    monkeypatch.setattr(dataset, "_retrieve", slow_retrieve)
    handle = dataset.download(destination=tmp_path, progressbar=False)

    assert started.wait(2)  # the worker thread really started
    assert handle.done() is False  # returned without waiting for it
    assert not (tmp_path / dataset.file).exists()
    # Consuming the path blocks until the worker finishes, then resolves.
    assert Path(os.fspath(handle)).read_bytes() == b"payload"
    assert handle.done() is True


def test_download_handle_propagates_errors(tmp_path):
    """A failed background download raises when the handle is consumed."""
    dataset = getattr(data, TINY_DATASET)()
    dataset.checksum = "md5:" + "0" * 32
    handle = dataset.download(destination=tmp_path, progressbar=False)
    with pytest.raises(Exception):
        os.fspath(handle)


def test_download_is_cached(tmp_path):
    """A second download of the same file must not refetch it."""
    dataset = getattr(data, TINY_DATASET)()
    first = dataset.download(destination=tmp_path, progressbar=False, background=False)
    mtime = (tmp_path / dataset.file).stat().st_mtime_ns
    second = dataset.download(destination=tmp_path, progressbar=False, background=False)
    assert first == second
    assert (tmp_path / dataset.file).stat().st_mtime_ns == mtime


def test_download_rejects_a_bad_checksum(tmp_path):
    """A wrong checksum must raise rather than hand back the file."""
    dataset = getattr(data, TINY_DATASET)()
    dataset.checksum = "md5:" + "0" * 32
    with pytest.raises(Exception):
        dataset.download(destination=tmp_path, progressbar=False, background=False)


@pytest.mark.slow
def test_download_ni_ebsd(tmp_path):
    dataset = NiEBSDLarge()
    dataset.download(destination=tmp_path, progressbar=False, background=False)
    assert (tmp_path / "patterns_v2.h5").exists()


@pytest.mark.slow
def test_download_mgo_nanocrystals(tmp_path):
    dataset = MgONanoCrystals()
    dataset.download(destination=tmp_path, progressbar=False, background=False)
    assert (tmp_path / dataset.file).exists()


@pytest.mark.slow
@pytest.mark.skipif(not QUANTEM_AVAILABLE, reason="quantem is not installed")
def test_quantem_loading(tmp_path):
    dataset = MgONanoCrystals()
    file_path = dataset.download(destination=tmp_path, progressbar=False, background=False)
    read_4dstem(file_path)
