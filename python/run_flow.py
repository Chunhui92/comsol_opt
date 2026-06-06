#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from comsol_opt.backends import make_backend
from comsol_opt.flow import run_flow


def main():
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow", default=str(repo_root / "configs" / "flow.yaml"))
    parser.add_argument("--params", help="Optional YAML parameter override, mainly used by calibration trials")
    parser.add_argument("--experiment", default=str(repo_root / "configs" / "experiments" / "bow_experiment.csv"))
    parser.add_argument("--backend", choices=["dryrun", "mock", "comsol"], default="mock")
    parser.add_argument("--comsol-command")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runs-root", default=str(repo_root / "runs"))
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--parameter-map", help="Optional legacy parameter map for old flow configs")
    args = parser.parse_args()

    backend = make_backend(args.backend, args.comsol_command)
    result = run_flow(
        flow_path=args.flow,
        params_path=args.params,
        experiment_path=args.experiment,
        backend=backend,
        run_id=args.run_id,
        runs_root=args.runs_root,
        repo_root=repo_root,
        use_cache=args.use_cache,
        parameter_map_path=args.parameter_map,
    )
    print(f"Completed {result['completed']} steps in {result['run_dir']}")
    latest_log = Path(result["run_dir"]) / f"S{result['completed']:02d}" / "logs" / "step.log"
    if latest_log.exists():
        print(f"Latest step log: {latest_log}")


if __name__ == "__main__":
    main()
