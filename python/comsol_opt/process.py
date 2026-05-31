from .rve import make_rve


def apply_process_update(step_input, state):
    params = step_input["parameters"]
    rule = step_input["update_rule"]
    step_id = step_input["step_id"]
    mat = state["materials_state"]

    if rule == "init":
        feol = params["FEOL"]
        mat["FEOL"]["sigma_init_x"] = feol["sigma_init_x"]
        mat["FEOL"]["sigma_init_y"] = feol["sigma_init_y"]
        state["wafer_inputs"]["FEOL"] = make_rve(
            step_id,
            "wafer_direct",
            sigx=feol["sigma_init_x"],
            sigy=feol["sigma_init_y"],
            rho=2330.0,
        )
    elif rule == "onon_deposition":
        onon = params["ONON"]
        mat["ONON"]["sigma_O_base"] = onon["sigma_O_base"]
        mat["ONON"]["sigma_N_base"] = onon["sigma_N_base"]
        mat["ONON"]["sigma_O_current"] = onon["sigma_O_base"]
        mat["ONON"]["sigma_N_current"] = onon["sigma_N_base"]
        _update_onon_layer(step_id, state)
    elif rule == "onon_trench_release":
        _release_onon(step_id, state, "trench_etch_ONON", params["release"]["trench_etch_ONON"])
    elif rule == "dpillar_onon_release":
        _release_onon(step_id, state, "dpillar_form_ONON", params["release"]["dpillar_form_ONON"])
    elif rule == "mat_remove_onon_release":
        _release_onon(step_id, state, "mat_remove_ONON", params["release"]["mat_remove_ONON"])
        state["wafer_inputs"]["aSi_layer"] = make_rve(step_id, "wafer_direct", active=False, valid=True)
    elif rule == "final":
        _release_onon(step_id, state, "final_ONON", params["release"]["final_ONON"])
    elif rule == "trench_ox_fill":
        mat["Ox"]["sigma_trench_fill"] = params["Ox"]["sigma_trench_fill"]
    elif rule == "pillar_diameter_update":
        state["geometry_state"]["pillar_diameter"] = params["geometry"]["pillar_diameter"]
    elif rule.startswith("pillar_dep"):
        dep = rule.replace("pillar_", "")
        mat["pillar_dep"][dep]["sigma_init"] = params["pillar_dep"][dep]["sigma_init"]
    elif rule == "asi_deposition":
        sigma = params["aSi"]["sigma"]
        mat["aSi"]["sigma"] = sigma
        state["wafer_inputs"]["aSi_layer"] = make_rve(step_id, "wafer_direct", sigx=sigma, sigy=0.92 * sigma, rho=2300.0, active=True)
    elif rule == "w_fill":
        mat["W"]["sigma_fill"] = params["W"]["sigma_fill"]


def _release_onon(step_id, state, key, factor):
    onon = state["materials_state"]["ONON"]
    onon["sigma_O_current"] *= factor
    onon["sigma_N_current"] *= factor
    onon["release_history"][step_id + "_" + key] = factor
    _update_onon_layer(step_id, state)


def _update_onon_layer(step_id, state):
    onon = state["materials_state"]["ONON"]
    sigx = 0.45 * onon["sigma_O_current"] + 0.55 * onon["sigma_N_current"]
    sigy = 0.50 * onon["sigma_O_current"] + 0.50 * onon["sigma_N_current"]
    state["wafer_inputs"]["ONON_layer"] = make_rve(step_id, "device_or_direct", sigx=sigx, sigy=sigy, rho=2800.0, active=True)
