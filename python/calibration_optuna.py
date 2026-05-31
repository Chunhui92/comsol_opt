#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from comsol_opt.calibration import run_optuna_or_fallback_calibration, run_quick_calibration


def main():
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow", default=str(repo_root / "configs" / "flow.yaml"))
    parser.add_argument("--params", default=str(repo_root / "configs" / "params_nominal.yaml"))
    parser.add_argument("--calibration-space", default=str(repo_root / "configs" / "calibration_space.yaml"))
    parser.add_argument("--experiment", default=str(repo_root / "exp" / "bow_experiment.csv"))
    parser.add_argument("--backend", choices=["mock", "comsol"], default="mock")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runs-root", default=str(repo_root / "runs"))
    parser.add_argument("--n-trials", type=int, default=5)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--optimizer", choices=["auto", "random"], default="auto")
    args = parser.parse_args()

    runner = run_optuna_or_fallback_calibration if args.optimizer == "auto" else run_quick_calibration
    result = runner(
        flow_path=args.flow,
        params_path=args.params,
        calibration_space_path=args.calibration_space,
        experiment_path=args.experiment,
        backend_name=args.backend,
        run_id=args.run_id,
        runs_root=args.runs_root,
        repo_root=repo_root,
        n_trials=args.n_trials,
        seed=args.seed,
    )
    print(f"best_loss={result['best_loss']:.6g} best_trial={result['best_trial']} dir={result['calib_dir']}")


if __name__ == "__main__":
    main()
