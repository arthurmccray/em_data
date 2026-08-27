"""Auto-generated dataset classes from YAML Files for downloading data."""

import os
from pathlib import Path

import yaml

from em_database._create_stubs import build_docstring
from em_database.downloadable_dataset import DownloadableDataset

# Map all the datasets in the "datasets" folder
# recursively travel down
__all__ = []
datasets_path = Path(__file__).parent.parent / "datasets"
for root, dirs, files in os.walk(datasets_path):
    for file in files:
        if file.endswith(".yaml") or file.endswith(".yml"):
            dataset_path = os.path.join(root, file)
            with open(dataset_path, "r") as f:
                data_dict_yaml = yaml.safe_load(f)
                for name in data_dict_yaml:
                    class_name = name.replace(" ", "_").replace("-", "_")
                    data_dict = data_dict_yaml[name]
                    _new_class = type(
                        class_name,
                        (DownloadableDataset,),
                        {"_spec": data_dict, "__doc__": build_docstring(data_dict)},
                    )

                    # Add to module globals and __all__
                    globals()[class_name] = _new_class
                    __all__.append(class_name)
