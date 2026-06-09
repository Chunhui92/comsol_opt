import csv
from pathlib import Path

from .config_io import load_json


def load_experiment(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return {row["step_id"]: row for row in csv.DictReader(handle)}


def write_summary(run_dir, steps, experiment):
    rows = []
    for step in steps:
        step_id = step["id"]
        state = load_json(Path(run_dir) / step_id / "state_out.json")
        rows.append(_v2_summary_row(step, state, experiment.get(step.get("experiment_step", step_id), {})))
    path = Path(run_dir) / "summary.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path

def _v2_summary_row(step, state, exp):
    rves = state.get("rve", {})
    onon = rves.get("onon", {})
    wafer = state["wafer_result"]
    return {
        "step_id": step["id"],
        "step_name": step["name"],
        "pillar_status": rves.get("pillar", {}).get("status", ""),
        "sc_status": rves.get("sc", {}).get("status", ""),
        "decap1_status": rves.get("decap1", {}).get("status", ""),
        "decap2_status": rves.get("decap2", {}).get("status", ""),
        "decap3_status": rves.get("decap3", {}).get("status", ""),
        "fecap_status": rves.get("fecap", {}).get("status", ""),
        "die_status": rves.get("die", {}).get("status", ""),
        "wafer_status": "run",
        "onon_source_step": onon.get("source_step", ""),
        "onon_source_node": onon.get("source_node", ""),
        "bow_x_um": wafer["bow_x_um"],
        "bow_y_um": wafer["bow_y_um"],
        "bow_x_exp_um": exp.get("bow_x_um", ""),
        "bow_y_exp_um": exp.get("bow_y_um", ""),
        "weight_x": exp.get("weight_x", 1.0),
        "weight_y": exp.get("weight_y", 1.0),
        "warnings": "",
        "errors": "",
    }
