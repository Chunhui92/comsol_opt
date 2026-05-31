import json
from pathlib import Path


def load_config(path):
    """Load JSON-compatible YAML, with PyYAML support when available."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        return json.loads(text)
    loaded = yaml.safe_load(text)
    return loaded if loaded is not None else {}


def dump_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

