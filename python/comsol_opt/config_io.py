import json
from pathlib import Path

import yaml


def load_config(path):
    """Load a YAML configuration file."""
    loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return loaded if loaded is not None else {}


def dump_data(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix in {".yaml", ".yml"}:
        path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
        return
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def dump_json(path, data):
    return dump_data(path, data)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
