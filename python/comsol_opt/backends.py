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
        subprocess.run(cmd, check=True)
        step_input = load_json(step_input_path)
        return load_json(Path(step_input["output_dir"]) / "step_result.json")


def make_backend(name, comsol_command=None):
    if name == "dryrun":
        return DryRunBackend()
    if name == "mock":
        return MockBackend()
    if name == "comsol":
        command = comsol_command.split() if isinstance(comsol_command, str) and comsol_command else None
        return ComsolBackend(command)
    raise ValueError(f"Unknown backend: {name}")

