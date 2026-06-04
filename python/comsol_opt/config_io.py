import json
from pathlib import Path


def load_config(path):
    """Load a YAML configuration file."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
    except ModuleNotFoundError:
        from ruamel.yaml import YAML  # type: ignore

        loaded = YAML(typ="safe").load(text)
    return loaded if loaded is not None else {}


def dump_data(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore

            path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
        except ModuleNotFoundError:
            from io import StringIO

            from ruamel.yaml import YAML  # type: ignore

            buffer = StringIO()
            yaml_writer = YAML()
            yaml_writer.default_flow_style = False
            yaml_writer.dump(data, buffer)
            path.write_text(buffer.getvalue(), encoding="utf-8")
        return
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def dump_json(path, data):
    return dump_data(path, data)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
