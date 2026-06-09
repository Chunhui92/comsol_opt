from pathlib import Path

from .parameter_txt import expression_to_float, parse_parameter_txt
from .v2_contract import matrix_to_upper21, upper21_to_matrix


D_UPPER_KEYS = (
    "D11",
    "D12",
    "D13",
    "D14",
    "D15",
    "D16",
    "D22",
    "D23",
    "D24",
    "D25",
    "D26",
    "D33",
    "D34",
    "D35",
    "D36",
    "D44",
    "D45",
    "D46",
    "D55",
    "D56",
    "D66",
)


def rve_parameter_names(slot):
    prefix = f"rve_{slot}"
    return [f"{prefix}_rho", f"{prefix}_sxx", f"{prefix}_syy", *[f"{prefix}_{key}" for key in D_UPPER_KEYS]]


def write_rve_parameter_txt(path, slot, rve):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"rve_{slot}"
    rows = [
        (f"{prefix}_rho", f"{float(rve['rho'])}[kg/m^3]", f"{slot} effective density"),
        (f"{prefix}_sxx", f"{float(rve['stress_eff']['sxx'])}[Pa]", f"{slot} residual stress xx"),
        (f"{prefix}_syy", f"{float(rve['stress_eff']['syy'])}[Pa]", f"{slot} residual stress yy"),
    ]
    rows.extend(
        (f"{prefix}_{key}", f"{value}[Pa]", f"{slot} stiffness {key}")
        for key, value in zip(D_UPPER_KEYS, matrix_to_upper21(rve["D"]))
    )
    path.write_text("\n".join(f"{name}\t{expr}\t{desc}" for name, expr, desc in rows) + "\n", encoding="utf-8")
    return path


def parse_rve_result_txt(path, source_step, source_model, previous_rve=None):
    params = parse_parameter_txt(path)
    missing = [key for key in ("rho", "sxx", "syy") if key not in params]
    if missing:
        raise ValueError(f"RVE result txt missing required fields: {missing}")
    d_values = []
    for key in D_UPPER_KEYS:
        if key not in params:
            if previous_rve is None:
                raise ValueError(f"RVE result txt missing stiffness field: {key}")
            d_values = None
            break
        d_values.append(params[key]["value"])
    matrix = previous_rve["D"] if d_values is None else upper21_to_matrix(d_values)
    return {
        "valid": True,
        "active": True,
        "source_step": source_step,
        "source_model": source_model,
        "rho": params["rho"]["value"],
        "stress_eff": {"sxx": params["sxx"]["value"], "syy": params["syy"]["value"]},
        "D": matrix,
        "meta": {"result_txt": str(path)},
    }


def write_rve_result_txt(path, rve):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        ("rho", f"{float(rve['rho'])}", "effective density kg/m^3"),
        ("sxx", f"{float(rve['stress_eff']['sxx'])}", "residual stress xx Pa"),
        ("syy", f"{float(rve['stress_eff']['syy'])}", "residual stress yy Pa"),
    ]
    rows.extend((key, f"{value}", f"stiffness {key} Pa") for key, value in zip(D_UPPER_KEYS, matrix_to_upper21(rve["D"])))
    path.write_text("\n".join(f"{name}\t{expr}\t{desc}" for name, expr, desc in rows) + "\n", encoding="utf-8")
    return path


def write_wafer_result_txt(path, wafer_result):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [(key, f"{float(wafer_result.get(key, 0.0))}", key) for key in ("bow_x_um", "bow_y_um", "kx", "ky")]
    path.write_text("\n".join(f"{name}\t{expr}\t{desc}" for name, expr, desc in rows) + "\n", encoding="utf-8")
    return path


def parse_wafer_result_txt(path):
    params = parse_parameter_txt(path)
    return {key: params.get(key, {"value": 0.0})["value"] for key in ("bow_x_um", "bow_y_um", "kx", "ky")}
