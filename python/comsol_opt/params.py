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

