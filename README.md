EM Data
-------

This is a simple project for aggregating different Electron Microscopy files which are hosted over different sources.  It uses pooch to download datasets and should be
used as a way to host simple example datasets for method validation.

Data is stored in a file "User/em_database" but this can be also set to a custom location.

List of datasets https://cssfrancis.github.io/em_data/datasets.html

## Installation

```bash
pip install em-database
```

## Usage

Every dataset is a class under `em_database.data`.  Calling `download()` fetches the
file to the data directory, verifies its checksum, and returns a path handle.  Files
that are already present are not downloaded again.

```python
import em_database.data as data
import hyperspy.api as hs

path = data.LayeredCuNb4DSTEM().download()
s = hs.load(path, lazy=True)
```

By default the download runs on a background thread so a notebook cell returns
immediately.  The handle it returns *is* the file path, so you can hand it straight
to a loader as above — it only blocks at the moment the file is actually opened.
Call `path.done()` to check progress without blocking, or `path.result()` to wait
explicitly.  Pass `download(background=False)` to block and get the path as a plain
string instead.

The download location defaults to `~/em_database` and can be changed either for the
session or through the `EM_DATABASE_DATA_DIR` environment variable:

```python
import em_database

em_database.set_data_dir("/path/to/somewhere")
em_database.get_data_dir()
em_database.reset_data_dir()
```

## Adding a dataset

Datasets are described by a YAML file in `em_database/datasets/`, one entry per file,
validated against `em_database/datasets/json-schema.json`.  The class name is generated
from the top-level key:

```yaml
MyDataset:
  description: What the data is, how it was acquired and how it is calibrated.
  source: https://zenodo.org/records/<record>/files
  checksum: md5:<hash>
  file: MyDataset.zspy
  data_size: 1.2 GB
  technique: 4D-STEM
  license: CC-BY-4.0
```

Open an issue with the new dataset template, or add the YAML file directly.
