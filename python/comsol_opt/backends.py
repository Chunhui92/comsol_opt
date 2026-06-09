import subprocess
from pathlib import Path

from .config_io import load_json
from .mock_comsol_worker import run_mock_step


class SimulationBackend:
    def run_step(self, step_input_path):
        raise NotImplementedError


class DryRunBackend(SimulationBackend):
    def run_step(self, step_input_path):
        return {"status": "dryrun", "step_input": str(step_input_path)}


class MockBackend(SimulationBackend):
    def run_step(self, step_input_path):
        return run_mock_step(step_input_path)


class ComsolBackend(SimulationBackend):
    def __init__(self, command=None):
        self.command = command

    def run_step(self, step_input_path):
        if not self.command:
            raise RuntimeError("COMSOL backend requires a command")
        cmd = [*self.command, str(step_input_path)]
        step_input = load_json(step_input_path)
        output_dir = Path(step_input["output_dir"])
        missing_parameter_txt = find_missing_parameter_txt(step_input)
        _append_step_log(
            output_dir,
            [
                f"backend=comsol step_id={step_input.get('step_id')} step_name={step_input.get('step_name')}",
                "command=" + " ".join(cmd),
            ],
        )
        if missing_parameter_txt:
            _append_step_log(output_dir, ["worker_status=blocked missing_parameter_txt=" + ",".join(missing_parameter_txt)])
            raise RuntimeError(
                "COMSOL backend has missing parameter txt files: " + ", ".join(missing_parameter_txt[:20])
            )
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as exc:
            _append_step_log(output_dir, [f"worker_status=failed returncode={exc.returncode}"])
            raise
        result = load_json(output_dir / "step_result.json")
        _append_step_log(output_dir, [f"worker_status={result.get('status', 'unknown')}"])
        return result


def make_backend(name, comsol_command=None):
    if name == "dryrun":
        return DryRunBackend()
    if name == "mock":
        return MockBackend()
    if name == "comsol":
        command = comsol_command.split() if isinstance(comsol_command, str) and comsol_command else None
        return ComsolBackend(command)
    raise ValueError(f"Unknown backend: {name}")


def _append_step_log(output_dir, lines):
    log_dir = Path(output_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "step.log").open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")


def find_missing_parameter_txt(step_input):
    missing = []
    produced = set()
    for run in step_input.get("runs", []):
        inputs = run.get("inputs", {})
        for slot, path in run.get("input_parameter_txt_paths", {}).items():
            source = inputs.get(slot, slot)
            if source in produced:
                continue
            if not Path(path).exists():
                missing.append(f"{run.get('node', '?')}.{slot}={path}")
        produced.add(run.get("node"))
    return missing
