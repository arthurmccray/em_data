import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Union

import pooch

# A single, lazily-created thread pool shared by every dataset. Background
# downloads run here so that a notebook cell returns immediately instead of
# blocking on the network. It is created on first use so that simply importing
# the package costs nothing.
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """Return the shared download thread pool, creating it on first use."""
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(
                    max_workers=4,
                    thread_name_prefix="em_database_download",
                )
    return _executor


# Subclass the *concrete* Path for this OS (WindowsPath / PosixPath) so that
# instances are real ``pathlib.Path`` objects. This matters: consumers such as
# ``hyperspy.load`` do ``isinstance(arg, Path)`` and reject anything else, so a
# plain ``Future`` (or a bare ``os.PathLike``) would not work as a drop-in for
# the download path — but a Path subclass does.
_ConcretePath = type(Path())


class DownloadFuture(_ConcretePath):
    """The path to a dataset that is downloading on a background thread.

    It is a genuine :class:`pathlib.Path` pointing at the file's final location
    (known before the download starts), so it can be passed anywhere a path is
    expected — ``hs.load(dataset.download())`` just works. The moment something
    actually touches the file (``open``, ``is_file``, ``os.fspath`` — the hooks
    every file reader ultimately goes through) it blocks until the download has
    finished, re-raising any error that occurred.

    For explicit control it also behaves like a future: ``done()`` checks status
    without blocking, ``result()``/``wait()`` block until the file is ready.
    """

    def _attach(self, future: "Future[str]") -> "DownloadFuture":
        self._future = future
        return self

    def __fspath__(self) -> str:
        # is_file()/exists()/stat()/open() and every os.fspath() consumer route
        # through here, so blocking here makes all of them wait for the bytes.
        future = getattr(self, "_future", None)
        if future is not None:
            future.result()  # blocks; re-raises a failed download
        return str(self)

    def result(self, timeout: Optional[float] = None) -> str:
        """Block until the download finishes and return the file path."""
        future = getattr(self, "_future", None)
        if future is not None:
            future.result(timeout)
        return str(self)

    def done(self) -> bool:
        """Return True if the download has finished (without blocking)."""
        future = getattr(self, "_future", None)
        return future.done() if future is not None else True

    def wait(self, timeout: Optional[float] = None) -> "DownloadFuture":
        """Block until the download finishes and return self (for chaining)."""
        self.result(timeout)
        return self

    def __repr__(self) -> str:  # never block just to display the object
        state = "done" if self.done() else "downloading"
        return f"<DownloadFuture {str(self)!r} [{state}]>"


class DownloadableDataset:
    def __init__(
        self,
        source: str,
        file: str,
        checksum: str = None,
        license: str = None,
        quality: str = None,
        data_size: str = None,
        doi: str = None,
        description: str = None,
        detector: Optional[str] = None,
        detector_manufacturer: Optional[str] = None,
        **kwargs,
    ):
        self.source = source
        self.file = file
        self.checksum = checksum
        self.license = license
        self.quality = quality
        self.doi = doi
        self.data_size = data_size
        self.description = description
        self.metadata = kwargs
        self.detector_manufacturer = detector_manufacturer
        self.detector = detector

    def __repr__(self):
        return f"<{self.__class__} url={self.source}/{self.file} bytes={self.data_size}>"

    def _repr_mimebundle_(self, **kwargs):
        """Rich display in Jupyter: an interactive card with download/metadata.

        Falls back to the plain repr if anywidget is not installed.
        """
        try:
            from em_database.widget import card

            widget = card(self)
        except Exception:
            return {"text/plain": repr(self)}
        return widget._repr_mimebundle_(**kwargs)

    @staticmethod
    def _resolve_destination(destination: str | None) -> str:
        """Return the directory the dataset should live in."""
        if destination is None:
            from em_database import config

            return config.data_dir()
        return destination

    def download(
        self,
        destination: str | None = None,
        progressbar: bool = True,
        chunk_size: int = 4096,
        background: bool = True,
    ) -> Union[str, DownloadFuture]:
        """Download the dataset to the specified destination if not already present.

        By default, this will download to the defined emdata.data_dir directory. You can set
        a custom default download directory with emdata.data_dir = 'your/path/here' which will
        in turn set the corresponding environment variable.

        If the file already exists in the destination directory and the checksum matches,
        it will not be downloaded again and the existing file path will be returned.

        Parameters
        ----------
        destination : str, optional
            The directory to download the dataset to. If None, uses the default emdata.data_dir
            directory, by default None.
        progressbar : bool, optional
            Whether to show a progress bar during download, by default True.
        chunk_size : int, optional
            The chunk size to use for downloading the file, by default 4096. Increasing this value will sometimes
            increase download speed at the cost of higher memory usage.
        background : bool, optional
            If True (the default), the download runs on a background thread and this
            returns immediately so a Jupyter cell stays responsive. The returned
            :class:`DownloadFuture` is a real path pointing at the file's final
            location, so you can hand it straight to a loader
            (``hs.load(dataset.download())``): it blocks only at the point the file
            is actually opened. Use ``.done()`` to poll and ``.result()`` to wait
            explicitly. If False, the download blocks and returns the file path as a
            plain string, exactly as before.

        Returns
        -------
        str or DownloadFuture
            When ``background`` is False, the local path to the downloaded file as a
            string. When ``background`` is True (the default), a :class:`DownloadFuture`
            path handle for the same location that resolves once the download finishes.
        """
        if not background:
            return self._retrieve(destination, progressbar, chunk_size)
        # Resolve where the file will end up: an existing shared/user copy if it
        # is already present, otherwise the user's download location.
        if destination is not None:
            target = os.path.join(destination, self.file)
        else:
            target = self.filepath() or os.path.join(self._resolve_destination(None), self.file)
        # In Jupyter (with the widget installed) a background download pops a
        # cancelable toast; the toast's monitor replaces the plain progress bar.
        monitor = finish = None
        if progressbar:
            try:
                from em_database.widget import _attach_toast

                monitor, finish = _attach_toast(type(self).__name__)
            except Exception:
                monitor = finish = None
        progress = monitor if monitor is not None else progressbar
        future = _get_executor().submit(self._retrieve, destination, progress, chunk_size)
        if finish is not None:
            future.add_done_callback(finish)
        return DownloadFuture(target)._attach(future)

    def _retrieve(
        self, destination: str | None = None, progressbar: bool = True, chunk_size: int = 4096
    ) -> str:
        """Fetch the file and return its local path (blocking).

        With no explicit destination, an existing system-wide/shared copy is used
        as-is (never re-downloaded); otherwise pooch downloads into the user's
        data directory.
        """
        if progressbar:
            try:
                import tqdm  # noqa: F401
            except ImportError:
                print("`tqdm` is not installed, progress bar will be disabled.")
                progressbar = False
        if destination is None:
            shared = self._find_shared()
            if shared is not None:
                return shared
            destination = self._resolve_destination(None)
        else:
            destination = self._resolve_destination(destination)
        # Instantiate an Http downloader with a custom user agent
        headers = {"User-Agent": "em_database (https://github.com/CSSFrancis/em_data)"}
        downloader = pooch.HTTPDownloader(
            progressbar=progressbar, chunk_size=chunk_size, headers=headers
        )
        filepath = pooch.retrieve(
            url=self.source + "/" + self.file,
            known_hash=self.checksum,
            fname=self.file,
            path=destination,
            downloader=downloader,
        )
        return filepath

    def _find_shared(self) -> str | None:
        """Path to an existing copy in a shared/system data dir, or None."""
        from em_database import config

        for directory in config.shared_data_dirs():
            candidate = os.path.join(directory, self.file)
            if os.path.exists(candidate):
                return candidate
        return None

    def filepath(self) -> str:
        """Return the local file path of the dataset if present.

        Looks in the shared/system data locations first, then the user's data
        directory. Returns None if the dataset is not downloaded anywhere."""
        from em_database import config

        for directory in config.data_search_dirs():
            candidate = os.path.join(directory, self.file)
            if os.path.exists(candidate):
                return candidate
        return None

    def delete(self, destination: str | None = None) -> bool:
        """Delete the downloaded file if it is present.

        Parameters
        ----------
        destination : str, optional
            The directory the dataset was downloaded to. If None, uses the
            default emdata.data_dir directory, by default None.

        Returns
        -------
        bool
            True if a file was removed, False if there was nothing to delete.
        """
        path = os.path.join(self._resolve_destination(destination), self.file)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False
