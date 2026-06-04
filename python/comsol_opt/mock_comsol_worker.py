from copy import deepcopy
from pathlib import Path

from .config_io import dump_data, load_json
from .process import apply_process_update
from .rve import make_rve, validate_rve
from .schemas import StepInput, WaferState
from .state import append_history, sync_legacy_state_aliases


def run_mock_step(step_input_path):
    step_input = load_json(step_input_path)
    StepInput.from_dict(step_input)
    state_in = load_json(step_input["state_in"])
    WaferState.from_dict(state_in)
    state_out = deepcopy(state_in)
    state_out["step_id"] = step_input["step_id"]
    state_out["step_name"] = step_input["step_name"]

    apply_process_update(step_input, state_out)

    if step_input.get("nodes"):
        return run_mock_dag_step(step_input, state_out)

    device_results = {}
    for dev_cfg in step_input.get("run_devices", []):
        rve = mock_device_rve(dev_cfg, step_input, state_out)
        validate_rve(rve, dev_cfg["name"])
        device_results[dev_cfg["name"]] = rve
        state_out["device_rves"][dev_cfg["name"]] = rve

    for mat_name in step_input.get("run_mats", []):
        device_key = step_input["mat_inputs"][mat_name]
        device_rve = device_results.get(device_key) or state_out["device_rves"][device_key]
        if not device_rve.get("valid"):
            raise RuntimeError(f"{step_input['step_id']} requires valid device RVE {device_key}")
        mat_rve = mock_mat_rve(mat_name, device_rve, step_input)
        validate_rve(mat_rve, mat_name)
        state_out["wafer_inputs"][mat_name] = mat_rve

    wafer_skipped = not step_input.get("run_wafer", True)
    if wafer_skipped:
        wafer_result = state_out["wafer_result"]
    else:
        wafer_result = mock_wafer_result(state_out, step_input)
        state_out["wafer_result"] = wafer_result
        append_history(state_out, step_input["step_id"], step_input["step_name"], wafer_result)

    output_dir = Path(step_input["output_dir"])
    dump_data(output_dir / "state_out.json", state_out)
    result = {
        "status": "success",
        "step_id": step_input["step_id"],
        "wafer_result": wafer_result,
        "wafer_skipped": wafer_skipped,
    }
    dump_data(output_dir / "step_result.json", result)
    return result


def run_mock_dag_step(step_input, state_out):
    output_dir = Path(step_input["output_dir"])
    manifest = {"step_id": step_input["step_id"], "nodes": {}}
    wafer_result = state_out["wafer_result"]
    wafer_skipped = True

    for node in step_input["nodes"]:
        node_id = node["id"]
        action = node["action"]
        entry = {"action": action}
        if action == "inherit":
            inherited = state_out.get("rve", {}).get(node_id)
            if not inherited or not inherited.get("valid"):
                raise RuntimeError(f"{step_input['step_id']} cannot inherit invalid RVE {node_id}")
            entry.update({"status": "success", "source_step": inherited["source_step"], "output": node["output"]})
            manifest["nodes"][node_id] = entry
            continue
        if action != "run":
            raise ValueError(f"{step_input['step_id']} node {node_id} has unknown action {action}")

        if node["type"] == "device":
            rve = mock_dag_device_rve(node, step_input, state_out)
            validate_rve(rve, node_id)
            state_out["rve"][node_id] = rve
            _write_node_result(output_dir, node, rve)
        elif node["type"] == "mat":
            source = _resolve_rve_reference(state_out, node["inputs"]["device"])
            if not source.get("valid"):
                raise RuntimeError(f"{step_input['step_id']} requires valid input for {node_id}")
            rve = mock_dag_mat_rve(node, source, step_input)
            validate_rve(rve, node_id)
            state_out["rve"][node_id] = rve
            _write_node_result(output_dir, node, rve)
        elif node["type"] == "die":
            inputs = [_resolve_rve_reference(state_out, ref) for ref in node["inputs"].values()]
            if any(not item.get("valid") for item in inputs):
                raise RuntimeError(f"{step_input['step_id']} requires valid die inputs")
            rve = mock_die_rve(node, inputs, step_input)
            validate_rve(rve, node_id)
            state_out["rve"][node_id] = rve
            _write_node_result(output_dir, node, rve)
        elif node["type"] == "wafer":
            if not step_input.get("run_wafer", True):
                entry.update({"status": "skipped", "output": node["output"]})
                manifest["nodes"][node_id] = entry
                continue
            wafer_skipped = False
            wafer_result = mock_dag_wafer_result(state_out, node)
            state_out["wafer_result"] = wafer_result
            append_history(state_out, step_input["step_id"], step_input["step_name"], wafer_result)
            _write_node_result(output_dir, node, wafer_result)
        else:
            raise ValueError(f"{step_input['step_id']} node {node_id} has unknown type {node['type']}")

        entry.update(
            {
                "status": "success",
                "template": node.get("template"),
                "source_step": step_input["step_id"],
                "output": node["output"],
            }
        )
        manifest["nodes"][node_id] = entry

    sync_legacy_state_aliases(state_out)
    dump_data(output_dir / "manifest.json", manifest)
    dump_data(output_dir / "state_out.json", state_out)
    result = {
        "status": "success",
        "step_id": step_input["step_id"],
        "wafer_result": wafer_result,
        "wafer_skipped": wafer_skipped,
    }
    dump_data(output_dir / "step_result.json", result)
    return result


def _write_node_result(output_dir, node, result):
    result_file = node.get("result_file")
    if result_file:
        dump_data(Path(output_dir) / result_file, result)


def _resolve_rve_reference(state, ref):
    if not ref.startswith("rve."):
        raise ValueError(f"Unsupported RVE reference: {ref}")
    return state["rve"][ref.split(".", 1)[1]]


def mock_dag_device_rve(node, step_input, state):
    mat = state["materials_state"]
    geo = state["geometry_state"]
    onon = mat["ONON"]
    sigma_onon = 0.5 * (onon["sigma_O_current"] + onon["sigma_N_current"])
    sigma_w = mat.get("W", {}).get("sigma_fill", 0.0)
    diameter = geo.get("pillar_diameter", 120e-9)
    diameter_factor = diameter / 120e-9
    device_factor = {"device_main": 1.0, "device_aux": 0.72, "onon_device": 0.55}.get(node["id"], 1.0)
    sigx = device_factor * (0.7 * sigma_onon + 0.3 * sigma_w) * diameter_factor
    sigy = device_factor * (0.65 * sigma_onon + 0.25 * sigma_w) / diameter_factor
    return make_rve(
        source_step=step_input["step_id"],
        source_model=node.get("template", node["id"]),
        sigx=sigx,
        sigy=sigy,
        d11=100e9 * diameter_factor,
        d22=100e9 / diameter_factor,
        rho=3000.0,
    )


def mock_dag_mat_rve(node, device_rve, step_input):
    mat_factor = {"mat1": 0.8, "mat2": 1.0, "mat3": 1.2, "mat4": 0.65}.get(node["id"], 1.0)
    return make_rve(
        source_step=step_input["step_id"],
        source_model=node.get("template", node["id"]),
        sigx=mat_factor * device_rve["stress_eff"]["sxx"],
        sigy=mat_factor * device_rve["stress_eff"]["syy"],
        d11=mat_factor * device_rve["D"][0][0],
        d22=mat_factor * device_rve["D"][1][1],
        rho=3200.0,
    )


def mock_die_rve(node, inputs, step_input):
    sigx = sum(item["stress_eff"]["sxx"] for item in inputs) / len(inputs)
    sigy = sum(item["stress_eff"]["syy"] for item in inputs) / len(inputs)
    d11 = sum(item["D"][0][0] for item in inputs) / len(inputs)
    d22 = sum(item["D"][1][1] for item in inputs) / len(inputs)
    return make_rve(
        source_step=step_input["step_id"],
        source_model=node.get("template", node["id"]),
        sigx=sigx,
        sigy=sigy,
        d11=d11,
        d22=d22,
        rho=3100.0,
    )


def mock_dag_wafer_result(state, node):
    die = _resolve_rve_reference(state, node["inputs"]["die"])
    onon = _resolve_rve_reference(state, node["inputs"]["onon"])
    bow_x = 0.7 * die["stress_eff"]["sxx"] / 1e6 + 0.3 * onon["stress_eff"]["sxx"] / 1e6
    bow_y = 0.7 * die["stress_eff"]["syy"] / 1e6 + 0.3 * onon["stress_eff"]["syy"] / 1e6
    return {
        "bow_x_um": 0.8 * bow_x,
        "bow_y_um": 0.75 * bow_y,
        "kx": bow_x * 1e-5,
        "ky": bow_y * 1e-5,
    }


def mock_device_rve(dev_cfg, step_input, state):
    mat = state["materials_state"]
    geo = state["geometry_state"]
    onon = mat["ONON"]
    sigma_onon = 0.5 * (onon["sigma_O_current"] + onon["sigma_N_current"])
    sigma_w = mat.get("W", {}).get("sigma_fill", 0.0)
    diameter = geo.get("pillar_diameter", 120e-9)
    diameter_factor = diameter / 120e-9
    device_factor = 0.8 if dev_cfg["name"] == "device2_for_mat3" else 1.0
    sigx = device_factor * (0.7 * sigma_onon + 0.3 * sigma_w) * diameter_factor
    sigy = device_factor * (0.65 * sigma_onon + 0.25 * sigma_w) / diameter_factor
    return make_rve(
        source_step=step_input["step_id"],
        source_model=dev_cfg["template_key"],
        sigx=sigx,
        sigy=sigy,
        d11=100e9 * diameter_factor,
        d22=100e9 / diameter_factor,
        rho=3000.0,
    )


def mock_mat_rve(mat_name, device_rve, step_input):
    mat_factor = {"mat1": 0.8, "mat2": 1.0, "mat3": 1.2}[mat_name]
    sigx = mat_factor * device_rve["stress_eff"]["sxx"]
    sigy = mat_factor * device_rve["stress_eff"]["syy"]
    return make_rve(
        source_step=step_input["step_id"],
        source_model=f"{mat_name}_template",
        sigx=sigx,
        sigy=sigy,
        d11=mat_factor * device_rve["D"][0][0],
        d22=mat_factor * device_rve["D"][1][1],
        rho=3200.0,
    )


def mock_wafer_result(state, step_input):
    weights = {
        "FEOL": 0.30,
        "mat1": 0.20,
        "mat2": 0.25,
        "mat3": 0.25,
        "ONON_layer": 0.15,
        "aSi_layer": 0.10,
    }
    bow_x = 0.0
    bow_y = 0.0
    for slot, rve in state["wafer_inputs"].items():
        if not rve.get("valid") or not rve.get("active", True):
            continue
        bow_x += weights.get(slot, 0.0) * rve["stress_eff"]["sxx"] / 1e6
        bow_y += weights.get(slot, 0.0) * rve["stress_eff"]["syy"] / 1e6
    if step_input["templates"]["wafer"].endswith("wafer_with_asi_template.mph"):
        bow_x *= 1.05
        bow_y *= 1.05
    return {
        "bow_x_um": 0.8 * bow_x,
        "bow_y_um": 0.75 * bow_y,
        "kx": bow_x * 1e-5,
        "ky": bow_y * 1e-5,
    }
