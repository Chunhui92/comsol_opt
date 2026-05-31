import csv
import json
import random
import shutil
from pathlib import Path

from .backends import make_backend
from .config_io import dump_json, load_config
from .flow import run_flow
from .loss import compute_loss_from_rows, load_summary_rows
from .params import copy_params, flatten_params, set_param


def all_parameter_specs(calibration_space):
    specs = []
    for group in calibration_space["groups"]:
        for spec in group["params"]:
            item = dict(spec)
            item["group"] = group["name"]
            item["start_step"] = group["start_step"]
            specs.append(item)
    return specs


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


def select_groups(calibration_space, stages):
    groups = calibration_space["groups"]
    if not stages:
        return groups
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
        selected.extend(matches)
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
):
    base_params = load_config(params_path)
    calibration_space = load_config(calibration_space_path)
    groups = select_groups(calibration_space, stages)
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
            )

        current_params = stage_result["params"]
        dump_json(stage_dir / "best_params.yaml", current_params)
        shutil.copyfile(stage_result["summary"], stage_dir / "best_summary.csv")
        all_history.extend(stage_result["history"])
        _write_history(stage_dir / "calibration_history.csv", stage_result["history"])
        _write_stage_log(
            stage_dir / "stage.log",
            [f"best_trial={stage_result['trial']}", f"best_loss={stage_result['loss']}"],
        )

    dump_json(out_dir / "final_params.yaml", current_params)
    _write_history(out_dir / "calibration_history.csv", all_history)
    return {"calib_dir": calib_dir, "completed_stages": len(groups), "final_params": current_params}


def _run_random_stage(current_params, specs, flow_path, experiment_path, backend_name, repo_root, trials_root, n_trials, seed, target_step, stage_dir):
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
            "random",
        )
        history.append(trial_result["record"])
        if best is None or trial_result["loss"] < best["loss"]:
            best = trial_result
    best["history"] = history
    return best


def _run_optuna_stage(current_params, specs, flow_path, experiment_path, backend_name, repo_root, trials_root, n_trials, seed, target_step, stage_dir):
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
            "optuna",
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


def _run_stage_trial(params, trial_index, flow_path, experiment_path, backend_name, repo_root, trials_root, target_step, stage_dir, optimizer_name):
    trial_dir = trials_root / f"trial_{trial_index:04d}"
    trial_dir.mkdir(parents=True, exist_ok=True)
    params_file = trial_dir / "params_trial.yaml"
    dump_json(params_file, params)
    result = run_flow(
        flow_path=flow_path,
        params_path=params_file,
        experiment_path=experiment_path,
        backend=make_backend(backend_name),
        run_id="flow",
        runs_root=trial_dir,
        repo_root=repo_root,
        use_cache=True,
    )
    summary_path = result["run_dir"] / "summary.csv"
    loss = loss_for_stage(summary_path, target_step)
    record = {
        "trial": trial_index,
        "stage": stage_dir.name,
        "optimizer": optimizer_name,
        "target_step": target_step,
        "loss": loss,
        "run_dir": str(result["run_dir"]),
    }
    _write_stage_log(stage_dir / "stage.log", [json.dumps(record, sort_keys=True)])
    return {"loss": loss, "params": params, "summary": summary_path, "trial": trial_index, "record": record}


def run_quick_calibration(flow_path, params_path, calibration_space_path, experiment_path, backend_name, run_id, runs_root, repo_root, n_trials, seed=17):
    base_params = load_config(params_path)
    calibration_space = load_config(calibration_space_path)
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
        dump_json(params_file, params)
        backend = make_backend(backend_name)
        result = run_flow(
            flow_path=flow_path,
            params_path=params_file,
            experiment_path=experiment_path,
            backend=backend,
            run_id="flow",
            runs_root=trial_dir,
            repo_root=repo_root,
            use_cache=True,
        )
        summary_path = result["run_dir"] / "summary.csv"
        loss = compute_loss_from_rows(load_summary_rows(summary_path))["loss"]
        record = {"trial": trial_index, "stage": "quick_global", "loss": loss, "run_dir": str(result["run_dir"])}
        history.append(record)
        if best is None or loss < best["loss"]:
            best = {"loss": loss, "params": params, "summary": summary_path, "trial": trial_index}

    _write_history(calib_dir / "calibration_history.csv", history)
    dump_json(calib_dir / "best_params.yaml", best["params"])
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
        )

    base_params = load_config(params_path)
    calibration_space = load_config(calibration_space_path)
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
        dump_json(params_file, params)
        result = run_flow(
            flow_path=flow_path,
            params_path=params_file,
            experiment_path=experiment_path,
            backend=make_backend(backend_name),
            run_id="flow",
            runs_root=trial_dir,
            repo_root=repo_root,
            use_cache=True,
        )
        summary_path = result["run_dir"] / "summary.csv"
        loss = compute_loss_from_rows(load_summary_rows(summary_path))["loss"]
        history.append({"trial": trial.number, "stage": "optuna_global", "loss": loss, "run_dir": str(result["run_dir"])})
        if best is None or loss < best["loss"]:
            best = {"loss": loss, "params": params, "summary": summary_path, "trial": trial.number}
        return loss

    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="minimize", sampler=sampler)
    nominal = flatten_params(base_params)
    study.enqueue_trial({spec["key"]: nominal[spec["key"]] for spec in specs})
    study.optimize(objective, n_trials=n_trials)
    _write_history(calib_dir / "calibration_history.csv", history)
    dump_json(calib_dir / "best_params.yaml", best["params"])
    shutil.copyfile(best["summary"], calib_dir / "best_summary.csv")
    return {"calib_dir": calib_dir, "best_loss": best["loss"], "best_trial": best["trial"]}


def _write_history(path, history):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = ["trial", "stage", "optimizer", "target_step", "loss", "run_dir"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(history)


def _write_stage_log(path, lines, mode="a"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode, encoding="utf-8") as handle:
        for line in lines:
            handle.write(str(line) + "\n")
