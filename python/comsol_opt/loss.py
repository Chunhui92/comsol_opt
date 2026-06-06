import csv
from pathlib import Path


def compute_loss_from_rows(rows, scale_x=30.0, scale_y=30.0, lambda_aniso=0.0, scale_aniso=30.0):
    total = 0.0
    count = 0
    for row in rows:
        if not row.get("bow_x_exp_um") or not row.get("bow_y_exp_um"):
            continue
        sim_x = float(row.get("bow_x_sim_um", row.get("bow_x_um")))
        sim_y = float(row.get("bow_y_sim_um", row.get("bow_y_um")))
        exp_x = float(row["bow_x_exp_um"])
        exp_y = float(row["bow_y_exp_um"])
        wx = float(row.get("weight_x", 1.0) or 1.0)
        wy = float(row.get("weight_y", 1.0) or 1.0)
        total += wx * ((sim_x - exp_x) / scale_x) ** 2
        total += wy * ((sim_y - exp_y) / scale_y) ** 2
        if lambda_aniso:
            total += lambda_aniso * (((sim_x - sim_y) - (exp_x - exp_y)) / scale_aniso) ** 2
        count += 1
    return {"loss": total, "n_steps": count}


def load_summary_rows(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))
