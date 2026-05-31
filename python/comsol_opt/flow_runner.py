from pathlib import Path

from .cache_manager import StepCache
from .config_io import dump_data, load_config
from .parameter_txt import ParameterTxtSet
from .params import flatten_params
from .state import initial_state
from .step_input import build_step_input, validate_flow
from .summary import load_experiment, write_summary


DEFAULT_PARAMETER_MAP_PATH = "configs/parameter_map.yaml"


def prepare_parameter_txt_set(repo_root, parameter_map_path=None):
    repo_root = Path(repo_root)
    config_path = Path(parameter_map_path) if parameter_map_path else repo_root / DEFAULT_PARAMETER_MAP_PATH
    parameter_map = load_config(config_path)
    return ParameterTxtSet(
        files={key: repo_root / value for key, value in parameter_map["parameter_files"].items()},
        mappings={
            key: {"file": value["file"], "name": value["txt_name"], "unit": value.get("unit", "")}
            for key, value in parameter_map["parameters"].items()
        },
    )


def run_flow(
    flow_path,
    params_path,
    experiment_path,
    backend,
    run_id,
    runs_root,
    repo_root,
    use_cache=False,
    parameter_map_path=None,
    cache_root=None,
):
    flow = load_config(flow_path)
    params = load_config(params_path)
    validate_flow(flow)
    run_dir = Path(runs_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    initial_path = run_dir / "_initial_state.json"
    dump_data(initial_path, initial_state(params))
    state_in_path = initial_path

    txt_set = prepare_parameter_txt_set(repo_root, parameter_map_path)
    cache = StepCache(cache_root or run_dir / ".cache") if use_cache else None
    completed = 0
    for step in flow["steps"]:
        step_dir = run_dir / step["id"]
        step_dir.mkdir(parents=True, exist_ok=True)
        parameter_txt_paths = txt_set.write_trial_files(step_dir / "parameters", flatten_params(params))
        step_input = build_step_input(step, flow, params, state_in_path, step_dir, parameter_txt_paths)
        step_input_path = step_dir / "step_input.json"
        dump_data(step_input_path, step_input)
        result = None
        if cache is not None and backend.__class__.__name__ != "DryRunBackend":
            cache_key = cache.key_for(step_input_path)
            if cache.restore(cache_key, step_dir):
                result = {"status": "cache_hit", "step_id": step["id"]}
        if result is None:
            result = backend.run_step(step_input_path)
            if cache is not None and result.get("status") != "dryrun":
                cache.store(cache_key, step_dir)
        if result.get("status") != "dryrun":
            state_in_path = step_dir / "state_out.json"
        completed += 1

    if backend.__class__.__name__ != "DryRunBackend":
        experiment = load_experiment(experiment_path)
        write_summary(run_dir, flow["steps"], experiment)
    return {"run_dir": run_dir, "completed": completed}
