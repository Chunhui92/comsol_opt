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
            "run_devices": step_input["run_devices"],
            "run_mats": step_input["run_mats"],
            "mat_inputs": step_input["mat_inputs"],
            "wafer_inputs": step_input["wafer_inputs"],
            "parameters": step_input["parameters"],
            "state_in": state_in,
        }
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def restore(self, key, output_dir):
        cache_dir = self.cache_root / key
        state_src = cache_dir / "state_out.json"
        result_src = cache_dir / "step_result.json"
        if not state_src.exists() or not result_src.exists():
            return False
        output_dir = Path(output_dir)
        shutil.copyfile(state_src, output_dir / "state_out.json")
        shutil.copyfile(result_src, output_dir / "step_result.json")
        return True

    def store(self, key, output_dir):
        cache_dir = self.cache_root / key
        cache_dir.mkdir(parents=True, exist_ok=True)
        output_dir = Path(output_dir)
        shutil.copyfile(output_dir / "state_out.json", cache_dir / "state_out.json")
        shutil.copyfile(output_dir / "step_result.json", cache_dir / "step_result.json")
