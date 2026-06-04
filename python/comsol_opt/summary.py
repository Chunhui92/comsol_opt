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
                "wafer_template": _step_wafer_template(step),
                "updated_slots": ";".join(_step_updated_slots(step)),
            }
        )
    path = Path(run_dir) / "summary.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _step_wafer_template(step):
    if "wafer_template" in step:
        return step["wafer_template"]
    for node in step.get("nodes", []):
        if node.get("type") == "wafer":
            return node.get("template", "")
    return ""


def _step_updated_slots(step):
    if "wafer_inputs" in step:
        return step["wafer_inputs"].get("update", [])
    return [node["id"] for node in step.get("nodes", []) if node.get("action") == "run"]
