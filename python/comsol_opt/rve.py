import hashlib
import json


def stiffness_matrix(d11=100e9, d22=100e9, d33=100e9, shear=35e9):
    return [
        [float(d11), 30e9, 30e9, 0.0, 0.0, 0.0],
        [30e9, float(d22), 30e9, 0.0, 0.0, 0.0],
        [30e9, 30e9, float(d33), 0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, float(shear), 0.0, 0.0],
        [0.0, 0.0, 0.0, 0.0, float(shear), 0.0],
        [0.0, 0.0, 0.0, 0.0, 0.0, float(shear)],
    ]


def make_rve(source_step, source_model, sigx=0.0, sigy=0.0, rho=0.0, active=True, valid=True, d11=100e9, d22=100e9):
    rve = {
        "valid": bool(valid),
        "active": bool(active),
        "source_step": source_step,
        "source_model": source_model,
        "rho": float(rho),
        "stress_eff": {"sxx": float(sigx), "syy": float(sigy)},
        "D": stiffness_matrix(d11=d11, d22=d22),
        "meta": {},
    }
    rve["meta"]["hash"] = rve_hash(rve)
    return rve


def empty_rve(slot):
    return make_rve("INIT", slot, valid=False, active=False, rho=0.0, d11=0.0, d22=0.0)


def rve_hash(rve):
    payload = {
        "valid": rve.get("valid"),
        "active": rve.get("active"),
        "source_step": rve.get("source_step"),
        "source_model": rve.get("source_model"),
        "rho": rve.get("rho"),
        "stress_eff": rve.get("stress_eff"),
        "D": rve.get("D"),
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def validate_rve(rve, name="rve"):
    matrix = rve.get("D")
    if not isinstance(matrix, list) or len(matrix) != 6:
        raise ValueError(f"{name}.D must be a 6x6 matrix")
    for row in matrix:
        if not isinstance(row, list) or len(row) != 6:
            raise ValueError(f"{name}.D must be a 6x6 matrix")
        for value in row:
            float(value)

