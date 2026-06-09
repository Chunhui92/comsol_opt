import hashlib
import json
import shutil
from pathlib import Path

from .config_io import load_json


class StepCache:
    def __init__(self, cache_root):
        self.cache_root = Path(cache_root)

    def key_for(self, step_input_path):
        step_input = load_json(step_input_path)
        state_in = load_json(step_input["state_in"])
        payload = {
            "step_id": step_input["step_id"],
            "templates": step_input["templates"],
            "nodes": step_input.get("nodes", []),
            "parameters": step_input["parameters"],
            "parameter_txt_files": self._parameter_txt_payload(step_input),
            "state_in": state_in,
        }
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _parameter_txt_payload(self, step_input):
        paths = step_input.get("parameter_txt_paths", {})
        order = step_input.get("parameter_txt_order", list(paths))
        payload = []
        for key in order:
            path = paths.get(key)
            if path is None:
                continue
            parameter_path = Path(path)
            payload.append(
                {
                    "key": key,
                    "path": str(parameter_path),
                    "sha256": hashlib.sha256(parameter_path.read_bytes()).hexdigest(),
                }
            )
        return payload

    def restore(self, key, output_dir):
        cache_dir = self.cache_root / key
        state_src = cache_dir / "state_out.json"
        result_src = cache_dir / "step_result.json"
        if not state_src.exists() or not result_src.exists():
            return False
        output_dir = Path(output_dir)
        shutil.copyfile(state_src, output_dir / "state_out.json")
        shutil.copyfile(result_src, output_dir / "step_result.json")
        for path in cache_dir.iterdir():
            if path.name in {"state_out.json", "step_result.json"}:
                continue
            shutil.copyfile(path, output_dir / path.name)
        return True

    def store(self, key, output_dir):
        cache_dir = self.cache_root / key
        cache_dir.mkdir(parents=True, exist_ok=True)
        output_dir = Path(output_dir)
        shutil.copyfile(output_dir / "state_out.json", cache_dir / "state_out.json")
        shutil.copyfile(output_dir / "step_result.json", cache_dir / "step_result.json")
        for path in output_dir.glob("*.json"):
            if path.name in {"step_input.json", "state_out.json", "step_result.json"}:
                continue
            shutil.copyfile(path, cache_dir / path.name)
