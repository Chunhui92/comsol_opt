from copy import deepcopy

from .rve import empty_rve, make_rve


def initial_state(params):
    feol = params["FEOL"]
    state = {
        "step_id": "INIT",
        "step_name": "initial",
        "wafer_result": {"bow_x_um": 0.0, "bow_y_um": 0.0, "kx": 0.0, "ky": 0.0},
        "device_rves": {
            "device_default": empty_rve("device_default"),
            "device2_for_mat3": empty_rve("device2_for_mat3"),
        },
        "wafer_inputs": {
            "FEOL": make_rve(
                "S00",
                "wafer_direct",
                sigx=feol["sigma_init_x"],
                sigy=feol["sigma_init_y"],
                rho=2330.0,
                active=True,
            ),
            "mat1": empty_rve("mat1"),
            "mat2": empty_rve("mat2"),
            "mat3": empty_rve("mat3"),
            "ONON_layer": empty_rve("ONON_layer"),
            "aSi_layer": empty_rve("aSi_layer"),
        },
        "materials_state": {
            "FEOL": {
                "sigma_init_x": feol["sigma_init_x"],
                "sigma_init_y": feol["sigma_init_y"],
            },
            "ONON": {
                "sigma_O_base": params["ONON"]["sigma_O_base"],
                "sigma_N_base": params["ONON"]["sigma_N_base"],
                "sigma_O_current": 0.0,
                "sigma_N_current": 0.0,
                "release_history": {},
            },
            "Ox": {"sigma_trench_fill": params["Ox"]["sigma_trench_fill"]},
            "pillar_dep": deepcopy(params["pillar_dep"]),
            "aSi": {"sigma": params["aSi"]["sigma"]},
            "W": {"sigma_fill": params["W"]["sigma_fill"]},
        },
        "geometry_state": {"pillar_diameter": params["geometry"]["pillar_diameter"]},
        "history": [],
    }
    return state


def append_history(state, step_id, step_name, wafer_result):
    state["history"].append(
        {
            "step_id": step_id,
            "step_name": step_name,
            "bow_x_um": wafer_result["bow_x_um"],
            "bow_y_um": wafer_result["bow_y_um"],
        }
    )

