from copy import deepcopy

from .rve import empty_rve


def initial_state_v2(params):
    rve = {
        "pillar": empty_rve("pillar"),
        "sc": empty_rve("sc"),
        "decap1": empty_rve("decap1"),
        "decap2": empty_rve("decap2"),
        "decap3": empty_rve("decap3"),
        "fecap": empty_rve("fecap"),
        "die": empty_rve("die"),
        "onon": empty_rve("onon"),
    }
    return {
        "step_id": "INIT",
        "step_name": "initial",
        "wafer_result": {"bow_x_um": 0.0, "bow_y_um": 0.0, "kx": 0.0, "ky": 0.0},
        "rve": rve,
        "materials_state": {
            "FEOL": dict(params.get("FEOL", {})),
            "ONON": dict(params.get("ONON", {})),
            "release": dict(params.get("release", {})),
        },
        "geometry_state": dict(params.get("geometry", {})),
        "history": [],
    }


def append_history(state, step_id, step_name, wafer_result):
    state["history"].append(
        {
            "step_id": step_id,
            "step_name": step_name,
            "bow_x_um": wafer_result["bow_x_um"],
            "bow_y_um": wafer_result["bow_y_um"],
        }
    )
