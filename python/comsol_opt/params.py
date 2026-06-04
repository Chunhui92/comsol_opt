from copy import deepcopy


def flatten_params(params):
    flat = {}

    def visit(prefix, value):
        if isinstance(value, dict):
            for key, child in value.items():
                visit(f"{prefix}.{key}" if prefix else key, child)
        else:
            flat[prefix] = value

    visit("", params)
    return flat


def set_param(params, dotted_key, value):
    target = params
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = value


def copy_params(params):
    return deepcopy(params)


def model_params_from_comsol_txt(txt_params):
    value = lambda name, default=0.0: txt_params.get(name, {}).get("value", default)
    return {
        "FEOL": {
            "sigma_init_x": value("sigma_FEOL_x", 180e6),
            "sigma_init_y": value("sigma_FEOL_y", 160e6),
        },
        "ONON": {
            "sigma_O_base": value("sigma_O_base", 100e6),
            "sigma_N_base": value("sigma_N_base", -300e6),
        },
        "release": {
            "trench_etch_ONON": value("r_ONON_trench", 1.0),
            "dpillar_form_ONON": value("r_ONON_dpillar", 1.0),
            "mat_remove_ONON": value("r_ONON_remove", 1.0),
            "final_ONON": value("r_ONON_final", 1.0),
        },
        "Ox": {"sigma_trench_fill": value("sigma_Ox_fill", 0.0)},
        "geometry": {"pillar_diameter": value("pillar_diameter", 120e-9)},
        "pillar_dep": {
            "dep1": {"sigma_init": value("sigma_dep1", 0.0)},
            "dep2": {"sigma_init": value("sigma_dep2", 0.0)},
            "dep3": {"sigma_init": value("sigma_dep3", 0.0)},
            "dep4": {"sigma_init": value("sigma_dep4", 0.0)},
            "dep5": {"sigma_init": value("sigma_dep5", 0.0)},
        },
        "aSi": {"sigma": value("sigma_aSi", 0.0)},
        "W": {"sigma_fill": value("sigma_W_fill", 500e6)},
    }
