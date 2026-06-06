from copy import deepcopy
from pathlib import Path

from .config_io import dump_data, load_json
from .process import apply_process_update
from .rve import make_rve, validate_rve
from .schemas import StepInput, WaferState
from .state import append_history, sync_legacy_state_aliases
from .v2_contract import canonical_ref_name


def run_mock_step(step_input_path):
    step_input = load_json(step_input_path)
    StepInput.from_dict(step_input)
    state_in = load_json(step_input["state_in"])
    WaferState.from_dict(state_in)
    state_out = deepcopy(state_in)
    state_out["step_id"] = step_input["step_id"]
    state_out["step_name"] = step_input["step_name"]

    if _is_v2_step(step_input):
        return run_mock_v2_step(step_input, state_out, state_in)

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


def run_mock_v2_step(step_input, state_out, state_in):
    output_dir = Path(step_input["output_dir"])
    manifest = {"step_id": step_input["step_id"], "nodes": {}}
    previous_rves = deepcopy(state_in.get("rve", {}))
    state_out.setdefault("rve", {})
    wafer_result = state_out["wafer_result"]
    log_lines = [
        f"step_id={step_input['step_id']} step_name={step_input['step_name']}",
        f"output_dir={output_dir}",
        "node_order=" + ",".join(node["node"] for node in step_input["nodes"]),
    ]

    for node in step_input["nodes"]:
        node_id = node["node"]
        action = node["action"]
        entry = {"action": action}
        log_lines.append(
            f"node={node_id} action={action} template={node.get('template', '')} model_path={node.get('model_path', node.get('template_path', ''))}"
        )
        if action == "inherit":
            inherited = state_out["rve"].get(node_id)
            if not inherited or not inherited.get("valid"):
                raise RuntimeError(f"{step_input['step_id']} cannot inherit invalid RVE {node_id}")
            entry.update({"status": "success", "source_step": inherited.get("source_step"), "output": node["output"]})
            log_lines.append(_v2_rve_log_line("inherit", node_id, inherited))
            manifest["nodes"][node_id] = entry
            continue
        if action in {"default_from", "alias"}:
            source = node["source"]
            source_rve = deepcopy(state_out["rve"][source])
            source_rve["status"] = action
            source_rve["source_node"] = source
            state_out["rve"][node_id] = source_rve
            entry.update({"status": "success", "source_step": source_rve.get("source_step"), "source_node": source})
            log_lines.append(f"{action} node={node_id} source={source} source_step={source_rve.get('source_step')}")
            log_lines.append(_v2_rve_log_line(action, node_id, source_rve))
            manifest["nodes"][node_id] = entry
            continue
        if action != "run":
            raise ValueError(f"{step_input['step_id']} node {node_id} has unknown action {action}")

        if node_id == "wafer":
            log_lines.extend(_v2_input_log_lines(node, state_out))
            wafer_result = mock_v2_wafer_result(state_out, node)
            state_out["wafer_result"] = wafer_result
            append_history(state_out, step_input["step_id"], step_input["step_name"], wafer_result)
            _write_node_result(output_dir, node, wafer_result)
            log_lines.append(
                f"wafer bow_x_um={wafer_result['bow_x_um']} bow_y_um={wafer_result['bow_y_um']} kx={wafer_result['kx']} ky={wafer_result['ky']}"
            )
            entry.update({"status": "success", "template": node.get("template"), "output": node["output"]})
            manifest["nodes"][node_id] = entry
            continue

        log_lines.extend(_v2_input_log_lines(node, state_out))
        inputs = _resolve_v2_inputs(state_out, node)
        rve = mock_v2_rve(node, inputs, step_input)
        if node.get("studies", {}).get("cp") is None and node.get("merge", {}).get("D") == "inherit_previous":
            rve["D"] = deepcopy(previous_rves[node_id]["D"])
            rve["D_source_step"] = previous_rves[node_id].get("source_step")
            rve["status"] = "run_stress_only"
        else:
            rve["status"] = "run"
        rve["source_node"] = node_id
        validate_rve(rve, node_id)
        state_out["rve"][node_id] = rve
        _write_node_result(output_dir, node, rve)
        log_lines.append(_v2_rve_log_line("run", node_id, rve))
        entry.update(
            {
                "status": "success",
                "template": node.get("template"),
                "source_step": step_input["step_id"],
                "output": node["output"],
                "inputs": {
                    slot: {"source_step": item.get("source_step"), "source_node": item.get("source_node")}
                    for slot, item in inputs.items()
                },
            }
        )
        manifest["nodes"][node_id] = entry

    dump_data(output_dir / "manifest.json", manifest)
    dump_data(output_dir / "state_out.json", state_out)
    _write_step_log(output_dir, log_lines)
    result = {"status": "success", "step_id": step_input["step_id"], "wafer_result": wafer_result, "wafer_skipped": False}
    dump_data(output_dir / "step_result.json", result)
    return result


def _is_v2_step(step_input):
    return any("node" in node for node in step_input.get("nodes", []))


def _resolve_v2_inputs(state, node):
    inputs = {}
    for slot, input_cfg in node.get("inputs", {}).items():
        source = canonical_ref_name(input_cfg["source"])
        rve = state["rve"][source]
        if not rve.get("valid"):
            raise RuntimeError(f"requires valid input {input_cfg['source']}")
        inputs[slot] = rve
    return inputs


def mock_v2_rve(node, inputs, step_input):
    if inputs:
        sigx = sum(item["stress_eff"]["sxx"] for item in inputs.values()) / len(inputs)
        sigy = sum(item["stress_eff"]["syy"] for item in inputs.values()) / len(inputs)
        d11 = sum(item["D"][0][0] for item in inputs.values()) / len(inputs)
        d22 = sum(item["D"][1][1] for item in inputs.values()) / len(inputs)
    else:
        factor = {
            "pillar": 1.0,
            "sc": 0.8,
            "decap1": 0.9,
            "decap2": 1.0,
            "decap3": 1.1,
            "fecap": 0.7,
            "die": 1.0,
        }.get(node["node"], 1.0)
        sigx = factor * (10.0 + len(step_input["step_id"])) * 1e6
        sigy = factor * (8.0 + len(step_input["step_id"])) * 1e6
        d11 = factor * 100e9
        d22 = factor * 90e9
    return make_rve(
        source_step=step_input["step_id"],
        source_model=node.get("template", node["node"]),
        sigx=sigx,
        sigy=sigy,
        d11=d11,
        d22=d22,
        rho=3000.0,
    )


def mock_v2_wafer_result(state, node):
    if "die" not in node.get("inputs", {}) or "onon" not in node.get("inputs", {}):
        return {"bow_x_um": 0.0, "bow_y_um": 0.0, "kx": 0.0, "ky": 0.0}
    die = state["rve"][canonical_ref_name(node["inputs"]["die"]["source"])]
    onon = state["rve"][canonical_ref_name(node["inputs"]["onon"]["source"])]
    bow_x = 0.7 * die["stress_eff"]["sxx"] / 1e6 + 0.3 * onon["stress_eff"]["sxx"] / 1e6
    bow_y = 0.7 * die["stress_eff"]["syy"] / 1e6 + 0.3 * onon["stress_eff"]["syy"] / 1e6
    return {"bow_x_um": 0.8 * bow_x, "bow_y_um": 0.75 * bow_y, "kx": bow_x * 1e-5, "ky": bow_y * 1e-5}


def _v2_input_log_lines(node, state):
    lines = []
    for slot, input_cfg in node.get("inputs", {}).items():
        source = canonical_ref_name(input_cfg["source"])
        rve = state["rve"][source]
        lines.append(
            f"input slot={slot} source={input_cfg['source']} source_step={rve.get('source_step')} source_node={rve.get('source_node', source)}"
        )
        target = input_cfg.get("target", {})
        material = target.get("material", {})
        if material:
            lines.append(
                "material "
                f"component={material.get('component', '')} "
                f"tag={material.get('tag', '')} "
                f"density_group={material.get('density_group', '')} "
                f"density_property={material.get('density_property', '')} "
                f"elastic_group={material.get('elastic_group', '')} "
                f"elastic_property={material.get('elastic_property', '')} "
                f"D_format={material.get('D_format', '')} "
                f"elasticity_order={material.get('elasticity_order', '')}"
            )
        stress = target.get("stress", {})
        if stress:
            lines.append(
                "stress "
                f"type={stress.get('type', 'initial_stress')} "
                f"component={stress.get('component', '')} "
                f"physics={stress.get('physics', '')} "
                f"parent_feature={stress.get('parent_feature', '')} "
                f"feature={stress.get('feature', '')} "
                f"property={stress.get('property', '')} "
                f"stress_format={stress.get('stress_format', '')}"
            )
    return lines


def _v2_rve_log_line(event, node_id, rve):
    return (
        f"rve event={event} node={node_id} status={rve.get('status', '')} "
        f"source_step={rve.get('source_step', '')} source_node={rve.get('source_node', '')} "
        f"rho={rve.get('rho', '')} sxx={rve.get('stress_eff', {}).get('sxx', '')} "
        f"syy={rve.get('stress_eff', {}).get('syy', '')} "
        f"D11={_matrix_value(rve.get('D'), 0, 0)} D22={_matrix_value(rve.get('D'), 1, 1)} "
        f"D_source_step={rve.get('D_source_step', '')}"
    )


def _matrix_value(matrix, row, col):
    if not matrix:
        return ""
    return matrix[row][col]


def _write_step_log(output_dir, lines):
    log_dir = Path(output_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "step.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
