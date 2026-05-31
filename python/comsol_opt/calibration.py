import csv
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
        writer = csv.DictWriter(handle, fieldnames=["trial", "stage", "loss", "run_dir"])
        writer.writeheader()
        writer.writerows(history)
