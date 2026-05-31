import csv
from pathlib import Path

from .config_io import dump_json, load_config, load_json
from .cache_manager import StepCache
from .parameter_txt import ParameterTxtSet
from .params import flatten_params
from .state import initial_state


DEFAULT_PARAM_TXT_FILES = {
    "struct": "params/comsol/struct.txt",
    "stress": "params/comsol/stress.txt",
    "temp": "params/comsol/temp.txt",
}

DEFAULT_PARAM_TXT_MAPPINGS = {
    "geometry.pillar_diameter": {"file": "struct", "name": "pillar_diameter"},
    "FEOL.sigma_init_x": {"file": "stress", "name": "sigma_feol_x"},
    "FEOL.sigma_init_y": {"file": "stress", "name": "sigma_feol_y"},
    "ONON.sigma_O_base": {"file": "stress", "name": "sigma_o_base"},
    "ONON.sigma_N_base": {"file": "stress", "name": "sigma_n_base"},
    "release.trench_etch_ONON": {"file": "stress", "name": "release_trench_etch_onon"},
    "release.dpillar_form_ONON": {"file": "stress", "name": "release_dpillar_form_onon"},
    "release.mat_remove_ONON": {"file": "stress", "name": "release_mat_remove_onon"},
    "release.final_ONON": {"file": "stress", "name": "release_final_onon"},
    "Ox.sigma_trench_fill": {"file": "stress", "name": "sigma_ox_trench_fill"},
    "pillar_dep.dep1.sigma_init": {"file": "stress", "name": "sigma_dep1"},
    "pillar_dep.dep2.sigma_init": {"file": "stress", "name": "sigma_dep2"},
    "pillar_dep.dep3.sigma_init": {"file": "stress", "name": "sigma_dep3"},
    "pillar_dep.dep4.sigma_init": {"file": "stress", "name": "sigma_dep4"},
    "pillar_dep.dep5.sigma_init": {"file": "stress", "name": "sigma_dep5"},
    "aSi.sigma": {"file": "stress", "name": "sigma_asi"},
    "W.sigma_fill": {"file": "stress", "name": "sigma_w_fill"},
    "process.temperature_C": {"file": "temp", "name": "process_temp"},
}


def load_experiment(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return {row["step_id"]: row for row in csv.DictReader(handle)}


def validate_flow(flow):
    device_templates = set(flow["templates"]["devices"])
    mat_templates = set(flow["templates"]["mats"])
    wafer_templates = set(flow["templates"]["wafers"])
    seen = set()
    for step in flow["steps"]:
        step_id = step["id"]
        if step_id in seen:
            raise ValueError(f"Duplicate step id: {step_id}")
        seen.add(step_id)
        if step["wafer_template"] not in wafer_templates:
            raise ValueError(f"{step_id} has unknown wafer template")
        for dev_cfg in step.get("run_devices", []):
            if dev_cfg["template_key"] not in device_templates:
                raise ValueError(f"{step_id} has unknown device template {dev_cfg['template_key']}")
        for mat_name in step.get("run_mats", []):
            if mat_name not in mat_templates:
                raise ValueError(f"{step_id} has unknown mat template {mat_name}")
            if mat_name not in step.get("mat_inputs", {}):
                raise ValueError(f"{step_id} missing mat input for {mat_name}")


def build_step_input(step, flow, params, state_in_path, output_dir, parameter_txt_paths):
    device_templates = flow["templates"]["devices"]
    return {
        "step_id": step["id"],
        "step_name": step["name"],
        "process_type": step["process_type"],
        "update_rule": step["update_rule"],
        "templates": {
            "devices": {
                dev["name"]: device_templates[dev["template_key"]]
                for dev in step.get("run_devices", [])
            },
            "mats": {mat: flow["templates"]["mats"][mat] for mat in flow["templates"]["mats"]},
            "wafer": flow["templates"]["wafers"][step["wafer_template"]],
        },
        "run_devices": step.get("run_devices", []),
        "run_mats": step.get("run_mats", []),
        "mat_inputs": step.get("mat_inputs", {}),
        "run_wafer": step.get("run_wafer", True),
        "wafer_inputs": step["wafer_inputs"],
        "parameters": params,
        "parameter_txt_paths": {key: str(value) for key, value in parameter_txt_paths.items()},
        "state_in": str(state_in_path),
        "output_dir": str(output_dir),
    }


def write_summary(run_dir, steps, experiment):
    rows = []
    for step in steps:
        step_id = step["id"]
        state = load_json(Path(run_dir) / step_id / "state_out.json")
        exp = experiment.get(step["experiment_step"], {})
        wafer = state["wafer_result"]
        rows.append(
            {
                "step_id": step_id,
                "step_name": step["name"],
                "bow_x_sim_um": wafer["bow_x_um"],
                "bow_y_sim_um": wafer["bow_y_um"],
                "bow_x_exp_um": exp.get("bow_x_um", ""),
                "bow_y_exp_um": exp.get("bow_y_um", ""),
                "weight_x": exp.get("weight_x", 1.0),
                "weight_y": exp.get("weight_y", 1.0),
                "wafer_template": step["wafer_template"],
                "updated_slots": ";".join(step["wafer_inputs"]["update"]),
            }
        )
    path = Path(run_dir) / "summary.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def prepare_parameter_txt_set(repo_root):
    return ParameterTxtSet(
        files={key: Path(repo_root) / value for key, value in DEFAULT_PARAM_TXT_FILES.items()},
        mappings=DEFAULT_PARAM_TXT_MAPPINGS,
    )


def run_flow(flow_path, params_path, experiment_path, backend, run_id, runs_root, repo_root, use_cache=False):
    flow = load_config(flow_path)
    params = load_config(params_path)
    validate_flow(flow)
    run_dir = Path(runs_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    initial_path = run_dir / "_initial_state.json"
    dump_json(initial_path, initial_state(params))
    state_in_path = initial_path

    txt_set = prepare_parameter_txt_set(repo_root)
    cache = StepCache(run_dir / ".cache") if use_cache else None
    completed = 0
    for step in flow["steps"]:
        step_dir = run_dir / step["id"]
        step_dir.mkdir(parents=True, exist_ok=True)
        parameter_txt_paths = txt_set.write_trial_files(step_dir / "parameters", flatten_params(params))
        step_input = build_step_input(step, flow, params, state_in_path, step_dir, parameter_txt_paths)
        step_input_path = step_dir / "step_input.json"
        dump_json(step_input_path, step_input)
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
