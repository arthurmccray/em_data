### Example datasets ###
import os
from em_database.downloadable_dataset import DownloadableDataset
from em_database._create_stubs import build_docstring
from em_database import data
__all__ = []


if "EM_DATABASE_DATA_DIR" not in os.environ:
    # set the default dir to User's home directory + "/emdata"
    os.environ["EM_DATABASE_DATA_DIR"] = os.path.join(
        os.path.expanduser("~"),"em_database"
    )



def get_data_dir():
    """
    Get the directory where example datasets are stored.

    Returns
    -------
    str
        Path to the example datasets directory.
    """
    return  os.environ["EM_DATABASE_DATA_DIR"]

def set_data_dir(path: str):
    """
    Set the directory where example datasets are stored.

    Parameters
    ----------
    path : str
        Path to the desired example datasets directory.
    """
    os.environ["EM_DATABASE_DATA_DIR"] = path

def reset_data_dir():
    """
    Reset the example datasets directory to the default location.
    """
    os.environ["EM_DATABASE_DATA_DIR"] = os.path.join(
        os.path.expanduser("~"),"em_database"
    )


def browse(**kwargs):
    """
    Open the interactive dataset browser in Jupyter.

    Returns an `anywidget` widget listing every dataset grouped by technique,
    showing which are downloaded, revealing full metadata on hover, and
    downloading on click with a live progress toast. Requires the optional
    `anywidget` dependency (`pip install em-database[widget]`).

    ``display(em_database)`` renders the same browser.
    """
    from em_database.widget import browse as _browse
    return _browse(**kwargs)


__all__ =  ['get_data_dir', 'set_data_dir', 'reset_data_dir', 'browse', "data"]


# Let ``display(em_database)`` render the browser. Reassigning the module's
# __class__ to a ModuleType subclass is a supported pattern (see PEP 562) and is
# what lets the package itself carry a rich Jupyter repr.
import sys as _sys
from types import ModuleType as _ModuleType


class _EmDatabaseModule(_ModuleType):
    def _repr_mimebundle_(self, include=None, exclude=None, **kwargs):
        try:
            widget = browse()
        except Exception:
            return {
                "text/plain": (
                    "em_database — install the interactive browser with "
                    "`pip install em-database[widget]`, then call "
                    "em_database.browse()."
                )
            }
        return widget._repr_mimebundle_(include=include, exclude=exclude, **kwargs)


_sys.modules[__name__].__class__ = _EmDatabaseModule