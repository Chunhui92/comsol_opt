import json
from pathlib import Path


def load_config(path):
    """Load a YAML configuration file."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    import yaml  # type: ignore

    loaded = yaml.safe_load(text)
    return loaded if loaded is not None else {}


def dump_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix in {".yaml", ".yml"}:
        import yaml  # type: ignore

        path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
        return
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
