import csv
import json
import random
import shutil
from pathlib import Path

from .backends import make_backend
from .config_io import dump_data, load_config
from .flow import run_flow
from .flow_runner import load_flow_definition
from .loss import compute_loss_from_rows, load_summary_rows
from .params import copy_params, flatten_params, set_param


CALIBRATION_TXT_NAMES = {
    "FEOL.sigma_init_x": "sigma_FEOL_x",
    "FEOL.sigma_init_y": "sigma_FEOL_y",
    "ONON.sigma_O_base": "sigma_O_base",
    "ONON.sigma_N_base": "sigma_N_base",
    "release.trench_etch_ONON": "r_ONON_trench",
    "release.dpillar_form_ONON": "r_ONON_dpillar",
    "release.mat_remove_ONON": "r_ONON_remove",
    "release.final_ONON": "r_ONON_final",
    "Ox.sigma_trench_fill": "sigma_Ox_fill",
    "geometry.pillar_diameter": "pillar_diameter",
    "pillar_dep.dep1.sigma_init": "sigma_dep1",
    "pillar_dep.dep2.sigma_init": "sigma_dep2",
    "pillar_dep.dep3.sigma_init": "sigma_dep3",
    "pillar_dep.dep4.sigma_init": "sigma_dep4",
    "pillar_dep.dep5.sigma_init": "sigma_dep5",
    "aSi.sigma": "sigma_aSi",
    "W.sigma_fill": "sigma_W_fill",
}


def all_parameter_specs(calibration_space):
    specs = []
    for group in calibration_space["groups"]:
        for spec in group["params"]:
            item = dict(spec)
            item["group"] = group["name"]
            item["start_step"] = group["start_step"]
            specs.append(item)
    return specs


def enabled_step_ids(flow_path, repo_root):
    flow = load_flow_definition(flow_path, repo_root)
    return {step["id"] for step in flow.get("steps", [])}


def active_calibration_space(calibration_space, flow_path, repo_root):
    active_steps = enabled_step_ids(flow_path, repo_root)
    if not active_steps:
        return calibration_space
    filtered = [
        group
        for group in calibration_space["groups"]
        if group.get("target_step", group.get("start_step")) in active_steps
    ]
    if not filtered:
        raise ValueError("No calibration groups target enabled flow steps")
    result = dict(calibration_space)
    result["groups"] = filtered
    return result


def get_param(params, dotted_key):
    value = params
    for part in dotted_key.split("."):
        value = value[part]
    return value


def parameter_regularization_loss(params, specs):
    total = 0.0
    for spec in specs:
        if "prior" not in spec or "scale" not in spec:
            continue
        scale = float(spec["scale"])
        if scale == 0.0:
            continue
        value = float(get_param(params, spec["key"]))
        prior = float(spec["prior"])
        total += ((value - prior) / scale) ** 2
    return total


def regularization_weight(calibration_space):
    return float(calibration_space.get("regularization", {}).get("lambda", 0.0))


def load_base_params(params_path, flow_path, repo_root):
    if params_path is not None:
        return load_config(params_path)
    flow = load_flow_definition(flow_path, repo_root)
    if flow.get("layout") == "v2" and flow["steps"]:
        return copy_params(flow["steps"][0]["parameters"])
    raise ValueError("Only v2 params-txt-driven flow configs are supported")


def deterministic_trial_params(base_params, specs, trial_index, seed):
    params = copy_params(base_params)
    if trial_index == 0:
        return params
    rng = random.Random(seed + trial_index)
    for spec in specs:
        value = rng.uniform(float(spec["low"]), float(spec["high"]))
        set_param(params, spec["key"], value)
    return params


def stage_trial_params(current_params, specs, trial_index, seed):
    params = copy_params(current_params)
    if trial_index == 0:
        return params
    rng = random.Random(seed + trial_index)
    for spec in specs:
        value = rng.uniform(float(spec["low"]), float(spec["high"]))
        set_param(params, spec["key"], value)
    return params


def select_groups(calibration_space, stages, flow_path=None, repo_root=None):
    groups = calibration_space["groups"]
    active_steps = enabled_step_ids(flow_path, repo_root) if flow_path is not None and repo_root is not None else None
    if not stages:
        if active_steps is None:
            return groups
        return [
            group
            for group in groups
            if group.get("target_step", group.get("start_step")) in active_steps
        ]
    requested = [item.strip() for item in stages.split(",") if item.strip()]
    selected = []
    for name in requested:
        matches = [
            group
            for group in groups
            if group["name"] == name or group.get("start_step") == name or group.get("target_step") == name
        ]
        if not matches:
            raise ValueError(f"Unknown calibration stage: {name}")
        for group in matches:
            target_step = group.get("target_step", group.get("start_step"))
            if active_steps is not None and target_step not in active_steps:
                raise ValueError(f"Calibration stage {group['name']} targets disabled or missing step {target_step}")
            selected.append(group)
    return selected


def loss_for_stage(summary_path, target_step):
    rows = [row for row in load_summary_rows(summary_path) if row["step_id"] == target_step]
    if not rows:
        raise ValueError(f"No summary row for target step {target_step}")
    return compute_loss_from_rows(rows)["loss"]


def run_staged_calibration(
    flow_path,
    params_path,
    calibration_space_path,
    experiment_path,
    backend_name,
    run_id,
    runs_root,
    repo_root,
    n_trials,
    seed=17,
    stages=None,
    optimizer="auto",
    comsol_command=None,
):
    base_params = load_base_params(params_path, flow_path, repo_root)
    calibration_space = load_config(calibration_space_path)
    lambda_reg = regularization_weight(calibration_space)
    groups = select_groups(calibration_space, stages, flow_path, repo_root)
    if not groups:
        raise ValueError("No calibration groups target enabled flow steps")
    calib_dir = Path(runs_root) / run_id
    out_dir = calib_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    current_params = copy_params(base_params)
    all_history = []

    for stage_index, group in enumerate(groups):
        stage_name = group["name"]
        target_step = group.get("target_step", group["start_step"])
        stage_dir = out_dir / f"{group['start_step']}_{stage_name}"
        trials_root = stage_dir / "trials"
        stage_dir.mkdir(parents=True, exist_ok=True)
        stage_specs = [dict(spec) for spec in group["params"]]
        for spec in stage_specs:
            spec["group"] = stage_name
            spec["start_step"] = group["start_step"]
        _write_stage_log(
            stage_dir / "stage.log",
            [
                f"stage={stage_name}",
                f"stage_index={stage_index}",
                f"start_step={group['start_step']}",
                f"target_step={target_step}",
                "params=" + ",".join(spec["key"] for spec in stage_specs),
                f"optimizer={optimizer}",
                f"n_trials={n_trials}",
            ],
            mode="w",
        )

        if optimizer == "auto":
            stage_result = _run_optuna_stage(
                current_params,
                stage_specs,
                flow_path,
                experiment_path,
                backend_name,
                repo_root,
                trials_root,
                n_trials,
                seed + stage_index * 1000,
                target_step,
                stage_dir,
                lambda_reg,
                comsol_command,
            )
        else:
            stage_result = _run_random_stage(
                current_params,
                stage_specs,
                flow_path,
                experiment_path,
                backend_name,
                repo_root,
                trials_root,
                n_trials,
                seed + stage_index * 1000,
                target_step,
                stage_dir,
                lambda_reg,
                comsol_command,
            )

        current_params = stage_result["params"]
        dump_data(stage_dir / "best_params.yaml", current_params)
        shutil.copyfile(stage_result["summary"], stage_dir / "best_summary.csv")
        all_history.extend(stage_result["history"])
        _write_history(stage_dir / "calibration_history.csv", stage_result["history"])
        _write_stage_log(
            stage_dir / "stage.log",
            [f"best_trial={stage_result['trial']}", f"best_loss={stage_result['loss']}"],
        )

    dump_data(out_dir / "final_params.yaml", current_params)
    _write_history(out_dir / "calibration_history.csv", all_history)
    return {"calib_dir": calib_dir, "completed_stages": len(groups), "final_params": current_params}


def _run_random_stage(
    current_params,
    specs,
    flow_path,
    experiment_path,
    backend_name,
    repo_root,
    trials_root,
    n_trials,
    seed,
    target_step,
    stage_dir,
    lambda_reg=0.0,
    comsol_command=None,
):
    best = None
    history = []
    for trial_index in range(n_trials):
        params = stage_trial_params(current_params, specs, trial_index, seed)
        trial_result = _run_stage_trial(
            params,
            trial_index,
            flow_path,
            experiment_path,
            backend_name,
            repo_root,
            trials_root,
            target_step,
            stage_dir,
            specs,
            lambda_reg,
            "random",
            comsol_command,
        )
        history.append(trial_result["record"])
        if best is None or trial_result["loss"] < best["loss"]:
            best = trial_result
    best["history"] = history
    return best


def _run_optuna_stage(
    current_params,
    specs,
    flow_path,
    experiment_path,
    backend_name,
    repo_root,
    trials_root,
    n_trials,
    seed,
    target_step,
    stage_dir,
    lambda_reg=0.0,
    comsol_command=None,
):
    try:
        import optuna  # type: ignore
    except ModuleNotFoundError:
        return _run_random_stage(
            current_params,
            specs,
            flow_path,
            experiment_path,
            backend_name,
            repo_root,
            trials_root,
            n_trials,
            seed,
            target_step,
            stage_dir,
            lambda_reg,
            comsol_command,
        )

    best = None
    history = []

    def objective(trial):
        nonlocal best
        params = copy_params(current_params)
        for spec in specs:
            value = trial.suggest_float(spec["key"], float(spec["low"]), float(spec["high"]))
            set_param(params, spec["key"], value)
        trial_result = _run_stage_trial(
            params,
            trial.number,
            flow_path,
            experiment_path,
            backend_name,
            repo_root,
            trials_root,
            target_step,
            stage_dir,
            specs,
            lambda_reg,
            "optuna",
            comsol_command,
        )
        history.append(trial_result["record"])
        if best is None or trial_result["loss"] < best["loss"]:
            best = trial_result
        return trial_result["loss"]

    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="minimize", sampler=sampler)
    nominal = flatten_params(current_params)
    study.enqueue_trial({spec["key"]: nominal[spec["key"]] for spec in specs})
    study.optimize(objective, n_trials=n_trials)
    best["history"] = history
    return best


def _run_stage_trial(
    params,
    trial_index,
    flow_path,
    experiment_path,
    backend_name,
    repo_root,
    trials_root,
    target_step,
    stage_dir,
    specs,
    lambda_reg,
    optimizer_name,
    comsol_command=None,
):
    trial_dir = trials_root / f"trial_{trial_index:04d}"
    trial_dir.mkdir(parents=True, exist_ok=True)
    params_file = trial_dir / "params_trial.yaml"
    dump_data(params_file, params)
    trial_repo_root, trial_flow_path = prepare_trial_flow_workspace(
        trial_dir,
        flow_path,
        repo_root,
        params,
        specs,
    )
    result = run_flow(
        flow_path=trial_flow_path,
        params_path=params_file,
        experiment_path=experiment_path,
        backend=make_backend(backend_name, comsol_command),
        run_id="flow",
        runs_root=trial_dir,
        repo_root=trial_repo_root,
        use_cache=True,
        cache_root=stage_dir / ".cache",
    )
    summary_path = result["run_dir"] / "summary.csv"
    bow_loss = loss_for_stage(summary_path, target_step)
    reg_loss = parameter_regularization_loss(params, specs)
    loss = bow_loss + lambda_reg * reg_loss
    record = {
        "trial": trial_index,
        "stage": stage_dir.name,
        "optimizer": optimizer_name,
        "target_step": target_step,
        "loss": loss,
        "bow_loss": bow_loss,
        "reg_loss": reg_loss,
        "run_dir": str(result["run_dir"]),
    }
    _write_stage_log(stage_dir / "stage.log", [json.dumps(record, sort_keys=True)])
    return {"loss": loss, "params": params, "summary": summary_path, "trial": trial_index, "record": record}


def run_quick_calibration(
    flow_path,
    params_path,
    calibration_space_path,
    experiment_path,
    backend_name,
    run_id,
    runs_root,
    repo_root,
    n_trials,
    seed=17,
    comsol_command=None,
):
    base_params = load_base_params(params_path, flow_path, repo_root)
    calibration_space = load_config(calibration_space_path)
    lambda_reg = regularization_weight(calibration_space)
    calibration_space = active_calibration_space(calibration_space, flow_path, repo_root)
    specs = all_parameter_specs(calibration_space)
    calib_dir = Path(runs_root) / run_id
    trials_root = calib_dir / "trials"
    calib_dir.mkdir(parents=True, exist_ok=True)
    history = []
    best = None

    for trial_index in range(n_trials):
        params = deterministic_trial_params(base_params, specs, trial_index, seed)
        trial_dir = trials_root / f"trial_{trial_index:04d}"
        trial_dir.mkdir(parents=True, exist_ok=True)
        params_file = trial_dir / "params_trial.yaml"
        dump_data(params_file, params)
        trial_repo_root, trial_flow_path = prepare_trial_flow_workspace(
            trial_dir,
            flow_path,
            repo_root,
            params,
            specs,
        )
        backend = make_backend(backend_name, comsol_command)
        result = run_flow(
            flow_path=trial_flow_path,
            params_path=params_file,
            experiment_path=experiment_path,
            backend=backend,
            run_id="flow",
            runs_root=trial_dir,
            repo_root=trial_repo_root,
            use_cache=True,
            cache_root=calib_dir / ".cache",
        )
        summary_path = result["run_dir"] / "summary.csv"
        bow_loss = compute_loss_from_rows(load_summary_rows(summary_path))["loss"]
        reg_loss = parameter_regularization_loss(params, specs)
        loss = bow_loss + lambda_reg * reg_loss
        record = {
            "trial": trial_index,
            "stage": "quick_global",
            "loss": loss,
            "bow_loss": bow_loss,
            "reg_loss": reg_loss,
            "run_dir": str(result["run_dir"]),
        }
        history.append(record)
        if best is None or loss < best["loss"]:
            best = {"loss": loss, "params": params, "summary": summary_path, "trial": trial_index}

    _write_history(calib_dir / "calibration_history.csv", history)
    dump_data(calib_dir / "best_params.yaml", best["params"])
    shutil.copyfile(best["summary"], calib_dir / "best_summary.csv")
    return {"calib_dir": calib_dir, "best_loss": best["loss"], "best_trial": best["trial"]}


def run_optuna_or_fallback_calibration(
    flow_path,
    params_path,
    calibration_space_path,
    experiment_path,
    backend_name,
    run_id,
    runs_root,
    repo_root,
    n_trials,
    seed=17,
    comsol_command=None,
):
    try:
        import optuna  # type: ignore
    except ModuleNotFoundError:
        return run_quick_calibration(
            flow_path,
            params_path,
            calibration_space_path,
            experiment_path,
            backend_name,
            run_id,
            runs_root,
            repo_root,
            n_trials,
            seed,
            comsol_command,
        )

    base_params = load_base_params(params_path, flow_path, repo_root)
    calibration_space = load_config(calibration_space_path)
    lambda_reg = regularization_weight(calibration_space)
    calibration_space = active_calibration_space(calibration_space, flow_path, repo_root)
    specs = all_parameter_specs(calibration_space)
    calib_dir = Path(runs_root) / run_id
    trials_root = calib_dir / "trials"
    calib_dir.mkdir(parents=True, exist_ok=True)
    history = []
    best = None

    def objective(trial):
        nonlocal best
        params = copy_params(base_params)
        for spec in specs:
            value = trial.suggest_float(spec["key"], float(spec["low"]), float(spec["high"]))
            set_param(params, spec["key"], value)
        trial_dir = trials_root / f"trial_{trial.number:04d}"
        trial_dir.mkdir(parents=True, exist_ok=True)
        params_file = trial_dir / "params_trial.yaml"
        dump_data(params_file, params)
        trial_repo_root, trial_flow_path = prepare_trial_flow_workspace(
            trial_dir,
            flow_path,
            repo_root,
            params,
            specs,
        )
        result = run_flow(
            flow_path=trial_flow_path,
            params_path=params_file,
            experiment_path=experiment_path,
            backend=make_backend(backend_name, comsol_command),
            run_id="flow",
            runs_root=trial_dir,
            repo_root=trial_repo_root,
            use_cache=True,
            cache_root=calib_dir / ".cache",
        )
        summary_path = result["run_dir"] / "summary.csv"
        bow_loss = compute_loss_from_rows(load_summary_rows(summary_path))["loss"]
        reg_loss = parameter_regularization_loss(params, specs)
        loss = bow_loss + lambda_reg * reg_loss
        history.append(
            {
                "trial": trial.number,
                "stage": "optuna_global",
                "loss": loss,
                "bow_loss": bow_loss,
                "reg_loss": reg_loss,
                "run_dir": str(result["run_dir"]),
            }
        )
        if best is None or loss < best["loss"]:
            best = {"loss": loss, "params": params, "summary": summary_path, "trial": trial.number}
        return loss

    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="minimize", sampler=sampler)
    nominal = flatten_params(base_params)
    study.enqueue_trial({spec["key"]: nominal[spec["key"]] for spec in specs})
    study.optimize(objective, n_trials=n_trials)
    _write_history(calib_dir / "calibration_history.csv", history)
    dump_data(calib_dir / "best_params.yaml", best["params"])
    shutil.copyfile(best["summary"], calib_dir / "best_summary.csv")
    return {"calib_dir": calib_dir, "best_loss": best["loss"], "best_trial": best["trial"]}


def prepare_trial_flow_workspace(trial_dir, flow_path, repo_root, params, specs):
    trial_dir = Path(trial_dir)
    repo_root = Path(repo_root)
    flow_path = Path(flow_path)
    trial_repo_root = trial_dir / "repo"
    trial_configs = trial_repo_root / "configs"
    if trial_configs.exists():
        shutil.rmtree(trial_configs)
    shutil.copytree(repo_root / "configs", trial_configs)
    override_path = trial_configs / "params" / "calibration_override.txt"
    override_path.write_text(calibration_override_txt(params, specs), encoding="utf-8")
    relative_flow = flow_path.relative_to(repo_root)
    return trial_repo_root, trial_repo_root / relative_flow


def calibration_override_txt(params, specs):
    flat = flatten_params(params)
    units = {spec["key"]: spec.get("unit", "") for spec in specs}
    lines = [
        "# Calibration override parameters",
        "# Generated by comsol_opt calibration.",
        "# Later values override global/step params.",
        "",
    ]
    for dotted_key in sorted(flat):
        txt_name = CALIBRATION_TXT_NAMES.get(dotted_key)
        if txt_name is None:
            continue
        unit = units.get(dotted_key, "")
        suffix = f"[{unit}]" if unit and unit != "ratio" else ""
        lines.append(f"{txt_name}\t{flat[dotted_key]}{suffix}\t{dotted_key}")
    return "\n".join(lines) + "\n"


def _write_history(path, history):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = ["trial", "stage", "optimizer", "target_step", "loss", "bow_loss", "reg_loss", "run_dir"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(history)


def _write_stage_log(path, lines, mode="a"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode, encoding="utf-8") as handle:
        for line in lines:
            handle.write(str(line) + "\n")
