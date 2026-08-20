"""An anywidget dataset browser for Jupyter: ``em_database.browse()``.

Renders every dataset grouped by technique, marks which are already downloaded
(○ / ●), shows metadata on hover, and downloads on click. Each download runs on
the shared background thread pool (so the kernel stays responsive) and reports
progress through a toast card with a cancel button - the same idea as a desktop
app's download manager, driven here through pooch's progress-bar hook.

anywidget is an optional dependency; :func:`browse` raises a helpful error if it
is not installed. Importing this module never imports anywidget at module load,
so ``import em_database`` stays cheap and dependency-light.
"""
from __future__ import annotations

import itertools
import threading
import time
from pathlib import Path

from em_database import catalogue as _catalogue
from em_database.downloadable_dataset import _get_executor

_STATIC = Path(__file__).parent / "static"

# How often (seconds) a running download pushes a progress update to the
# frontend, so a fast connection does not flood the widget comm.
_PROGRESS_INTERVAL = 0.15

_pooch_quieted = False


def _quiet_pooch():
    """Silence pooch's "Downloading data from …" INFO logs.

    In Jupyter those propagate to stderr and render as red output. The widget
    shows the same information as a toast, so once a widget is in use we don't
    need pooch's chatter. Warnings and errors are left intact. Idempotent.
    """
    global _pooch_quieted
    if _pooch_quieted:
        return
    try:
        import logging
        import pooch
        pooch.get_logger().setLevel(logging.WARNING)
        _pooch_quieted = True
    except Exception:
        pass


class DownloadCancelled(Exception):
    """Raised inside pooch's stream when the user cancels a download.

    It is not a ``ValueError``/requests error, so pooch's retry loop does not
    re-attempt a cancelled download; pooch streams to a temp file and deletes it
    on error, so no partial file is left in the cache.
    """


class _WidgetProgress:
    """A pooch-compatible progress object that pushes to the widget.

    pooch drives it per file: it assigns ``total`` before streaming, calls
    ``update(n)`` per chunk, then ``reset()`` + ``update(total)`` + ``close()``
    at the end. Raising from ``update`` when the cancel flag is set aborts the
    stream. Emits are throttled to :data:`_PROGRESS_INTERVAL`.
    """

    def __init__(self, widget, token, label, cancel):
        self._widget = widget
        self.token = token
        self.label = label
        self._cancel = cancel
        self._total = 0
        self._done = 0
        self._last_emit = 0.0
        self._closing = False

    # pooch assigns `progress.total = content_length` before streaming a file.
    @property
    def total(self):
        return self._total

    @total.setter
    def total(self, value):
        self._total = int(value or 0)
        self._done = 0
        self._closing = False
        self._emit(force=True)

    def update(self, n):
        if self._cancel.is_set():
            raise DownloadCancelled(self.token)
        self._done += int(n)
        if self._total:
            self._done = min(self._done, self._total)
        self._emit(force=self._closing)

    def reset(self):
        # pooch's end-of-file sequence is reset() -> update(total) -> close();
        # mark it so the final update emits un-throttled (bar reaches 100%).
        self._done = 0
        self._closing = True

    def close(self):
        self._closing = False

    def _emit(self, force=False):
        now = time.monotonic()
        if not force and (now - self._last_emit) < _PROGRESS_INTERVAL:
            return
        self._last_emit = now
        self._widget._set_progress(self.token, self.label, self._done, self._total)


def _make_browser_class():
    """Build the ``DatasetBrowser`` class, importing anywidget lazily."""
    import anywidget
    import traitlets

    class DatasetBrowser(anywidget.AnyWidget):
        """Interactive, hoverable list of the em-database datasets."""

        _esm = _STATIC / "browser.js"
        _css = _STATIC / "browser.css"

        # State the frontend renders. `downloads` maps a per-download token to
        # {label, done, total} while running, or {label, error} on failure.
        groups = traitlets.List().tag(sync=True)
        data_dir = traitlets.Unicode().tag(sync=True)
        n_downloaded = traitlets.Int().tag(sync=True)
        n_total = traitlets.Int().tag(sync=True)
        downloads = traitlets.Dict().tag(sync=True)
        # Commands from the frontend arrive as a synced dict (more reliable than
        # custom comm messages): the frontend bumps a nonce so repeats register.
        _command = traitlets.Dict().tag(sync=True)

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._lock = threading.RLock()
            self._cancels: dict[str, threading.Event] = {}
            self._counter = itertools.count()
            self.refresh()
            self.observe(self._on_command, names="_command")

        # -- catalogue state ------------------------------------------------
        def refresh(self):
            """Rebuild the catalogue - re-checks which files are on disk."""
            cat = _catalogue.catalogue()
            self.data_dir = cat["data_dir"]
            self.groups = cat["groups"]
            self.n_downloaded = cat["n_downloaded"]
            self.n_total = cat["n_total"]

        # -- commands from the frontend ------------------------------------
        # NB: do NOT name a method `_handle_msg` - that is ipywidgets' own
        # internal comm callback, and overriding it breaks all comm handling
        # (including trait sync). Commands arrive via the `_command` trait.
        def _on_command(self, change):
            self._dispatch(change.get("new") or {})

        def _dispatch(self, command):
            action = command.get("action") or command.get("type")
            if action == "download":
                self._start_download(str(command.get("name", "")))
            elif action == "cancel":
                self._cancel(str(command.get("token", "")))
            elif action == "dismiss":
                self._clear_progress(str(command.get("token", "")))
            elif action == "delete":
                self._delete(str(command.get("name", "")))
            elif action == "refresh":
                self.refresh()

        def _delete(self, name):
            """Delete a dataset's downloaded file and refresh its status."""
            ds = _catalogue.resolve(name)
            if ds is not None:
                try:
                    ds.delete()
                except Exception:
                    pass
                self.refresh()

        # -- downloads ------------------------------------------------------
        def _start_download(self, name):
            """Kick off a background download for ``name`` and show a toast."""
            ds = _catalogue.resolve(name)
            if ds is None:
                return None
            token = f"{name}-{next(self._counter)}"
            cancel = threading.Event()
            with self._lock:
                self._cancels[token] = cancel
            # Show the toast immediately - indeterminate until the first bytes,
            # and it also covers the cached case where no bytes ever flow.
            self._set_progress(token, name, 0, 0)
            monitor = _WidgetProgress(self, token, name, cancel)
            future = _get_executor().submit(
                ds.download, progressbar=monitor, background=False
            )
            future.add_done_callback(
                lambda f, tk=token, nm=name: self._finish_download(tk, nm, f)
            )
            return future

        def _finish_download(self, token, name, future):
            with self._lock:
                self._cancels.pop(token, None)
            error = future.exception()
            if error is not None and not isinstance(error, DownloadCancelled):
                self._set_error(token, name, str(error))
            else:
                self._clear_progress(token)
            self.refresh()

        def _cancel(self, token):
            with self._lock:
                event = self._cancels.get(token)
            if event is not None:
                event.set()

        # -- progress trait plumbing (called from worker threads) ----------
        # ipykernel routes widget comm messages through a dedicated iopub
        # thread, so assigning these traits from a pool thread is safe.
        def _set_progress(self, token, label, done, total):
            with self._lock:
                downloads = dict(self.downloads)
                downloads[token] = {"label": label, "done": int(done), "total": int(total)}
                self.downloads = downloads

        def _clear_progress(self, token):
            with self._lock:
                downloads = dict(self.downloads)
                if downloads.pop(token, None) is not None:
                    self.downloads = downloads

        def _set_error(self, token, name, message):
            with self._lock:
                downloads = dict(self.downloads)
                downloads[token] = {"label": name, "error": message}
                self.downloads = downloads

    return DatasetBrowser


def _make_card_class():
    """Build the ``DatasetCard`` class (one dataset), importing anywidget lazily."""
    import anywidget
    import traitlets

    class DatasetCard(anywidget.AnyWidget):
        """An interactive card for a single dataset - what ``display(ds)`` shows."""

        _esm = _STATIC / "card.js"
        _css = _STATIC / "browser.css"

        info = traitlets.Dict().tag(sync=True)      # the catalogue entry() dict
        download = traitlets.Dict().tag(sync=True)  # {label, done, total} | {} | {error}
        _command = traitlets.Dict().tag(sync=True)

        def __init__(self, dataset, **kwargs):
            super().__init__(**kwargs)
            self._dataset = dataset
            self._name = type(dataset).__name__
            self._lock = threading.RLock()
            self._cancel = None
            self._counter = itertools.count()
            self.refresh()
            self.observe(self._on_command, names="_command")

        def refresh(self):
            self.info = _catalogue.entry(self._name, self._dataset)

        def _on_command(self, change):
            command = change.get("new") or {}
            action = command.get("action")
            if action == "download":
                self._start_download()
            elif action == "cancel":
                with self._lock:
                    event = self._cancel
                if event is not None:
                    event.set()
            elif action == "dismiss":
                self.download = {}
            elif action == "delete":
                try:
                    self._dataset.delete()
                except Exception:
                    pass
                self.refresh()
            elif action == "refresh":
                self.refresh()

        def _start_download(self):
            with self._lock:
                if self._cancel is not None:
                    return  # already downloading
                self._cancel = threading.Event()
            token = f"{self._name}-{next(self._counter)}"
            self.download = {"label": self._name, "done": 0, "total": 0}
            monitor = _WidgetProgress(self, token, self._name, self._cancel)
            future = _get_executor().submit(
                self._dataset.download, progressbar=monitor, background=False
            )
            future.add_done_callback(self._finish_download)
            return future

        def _finish_download(self, future):
            with self._lock:
                self._cancel = None
            error = future.exception()
            if error is not None and not isinstance(error, DownloadCancelled):
                self.download = {"label": self._name, "error": str(error)}
            else:
                self.download = {}
            self.refresh()

        # Called from the worker thread by _WidgetProgress.
        def _set_progress(self, token, label, done, total):
            self.download = {"label": label, "done": int(done), "total": int(total)}

    return DatasetCard


_browser_class = None
_card_class = None


def card(dataset):
    """Return an interactive card widget for a single dataset (Jupyter).

    This backs ``display(dataset)`` / a dataset being the last line in a cell.
    Requires anywidget (``pip install em-database[widget]``).
    """
    _quiet_pooch()
    global _card_class
    if _card_class is None:
        try:
            _card_class = _make_card_class()
        except ImportError as error:
            raise ImportError(
                "Displaying a dataset needs anywidget. Install it with "
                "`pip install em-database[widget]` (or `pip install anywidget`)."
            ) from error
    return _card_class(dataset)


def browse(**kwargs):
    """Return an interactive dataset browser widget for Jupyter.

    Requires anywidget (``pip install em-database[widget]``). The widget lists
    every dataset grouped by technique, shows which are downloaded, reveals full
    metadata on hover, and downloads on click with a live progress toast.
    """
    _quiet_pooch()
    global _browser_class
    if _browser_class is None:
        try:
            _browser_class = _make_browser_class()
        except ImportError as error:
            raise ImportError(
                "em_database.browse() needs anywidget. Install it with "
                "`pip install em-database[widget]` (or `pip install anywidget`)."
            ) from error
    return _browser_class(**kwargs)
