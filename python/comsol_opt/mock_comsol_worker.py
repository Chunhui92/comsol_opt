from copy import deepcopy
from pathlib import Path

from .config_io import dump_data, load_json
from .rve import make_rve, validate_rve
from .rve_txt import write_rve_parameter_txt, write_rve_result_txt, write_wafer_result_txt
from .schemas import StepInput, WaferState
from .state import append_history
from .v2_contract import canonical_ref_name


def run_mock_step(step_input_path):
    step_input = load_json(step_input_path)
    StepInput.from_dict(step_input)
    state_in = load_json(step_input["state_in"])
    WaferState.from_dict(state_in)
    state_out = deepcopy(state_in)
    state_out["step_id"] = step_input["step_id"]
    state_out["step_name"] = step_input["step_name"]

    if not _is_v2_step(step_input):
        raise ValueError("Mock backend only supports v2 params-txt-driven step inputs")
    return run_mock_v2_step(step_input, state_out, state_in)


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
    interface_log = {
        "step_id": step_input["step_id"],
        "step_name": step_input["step_name"],
        "template_registry": step_input.get("template_registry", ""),
        "runs": [],
    }

    for node in step_input["nodes"]:
        node_id = node["node"]
        action = node["action"]
        entry = {"action": action}
        log_lines.append(
            f"node={node_id} action={action} template={node.get('template', '')} model_path={_v2_model_path(step_input, node)}"
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
            _write_v2_input_parameter_txts(node, state_out)
            log_lines.extend(_v2_input_log_lines(node, state_out, step_input))
            interface_log["runs"].append(_v2_interface_run(node, state_out, step_input))
            wafer_result = mock_v2_wafer_result(state_out, node)
            state_out["wafer_result"] = wafer_result
            append_history(state_out, step_input["step_id"], step_input["step_name"], wafer_result)
            _write_node_result(output_dir, node, wafer_result)
            _write_v2_output_txt(node, wafer_result)
            log_lines.append(
                f"wafer bow_x_um={wafer_result['bow_x_um']} bow_y_um={wafer_result['bow_y_um']} kx={wafer_result['kx']} ky={wafer_result['ky']}"
            )
            entry.update({"status": "success", "template": node.get("template"), "output": node["output"]})
            manifest["nodes"][node_id] = entry
            continue

        _write_v2_input_parameter_txts(node, state_out)
        log_lines.extend(_v2_input_log_lines(node, state_out, step_input))
        interface_log["runs"].append(_v2_interface_run(node, state_out, step_input))
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
        _write_v2_output_txt(node, rve)
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
    _write_interface_log(output_dir, interface_log)
    result = {"status": "success", "step_id": step_input["step_id"], "wafer_result": wafer_result, "wafer_skipped": False}
    dump_data(output_dir / "step_result.json", result)
    return result


def _is_v2_step(step_input):
    return any("node" in node for node in step_input.get("nodes", []))


def _resolve_v2_inputs(state, node):
    inputs = {}
    for slot, input_cfg in node.get("inputs", {}).items():
        source = _v2_input_source_name(input_cfg)
        rve = state["rve"][source]
        if not rve.get("valid"):
            raise RuntimeError(f"requires valid input rve.{source}")
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
    die = state["rve"][_v2_input_source_name(node["inputs"]["die"])]
    onon = state["rve"][_v2_input_source_name(node["inputs"]["onon"])]
    bow_x = 0.7 * die["stress_eff"]["sxx"] / 1e6 + 0.3 * onon["stress_eff"]["sxx"] / 1e6
    bow_y = 0.7 * die["stress_eff"]["syy"] / 1e6 + 0.3 * onon["stress_eff"]["syy"] / 1e6
    return {"bow_x_um": 0.8 * bow_x, "bow_y_um": 0.75 * bow_y, "kx": bow_x * 1e-5, "ky": bow_y * 1e-5}


def _v2_input_log_lines(node, state, step_input=None):
    lines = []
    for slot, input_cfg in node.get("inputs", {}).items():
        source = _v2_input_source_name(input_cfg)
        rve = state["rve"][source]
        source_ref = f"rve.{source}"
        if isinstance(input_cfg, dict):
            source_ref = input_cfg.get("source") or input_cfg.get("ref") or source_ref
        lines.append(
            f"input slot={slot} source={source_ref} source_step={rve.get('source_step')} source_node={rve.get('source_node', source)}"
        )
        parameter_path = node.get("input_parameter_txt_paths", {}).get(slot, "")
        lines.append(f"parameter_txt slot={slot} path={parameter_path}")
    if node.get("output_txt_path"):
        lines.append(f"output_txt={node['output_txt_path']}")
    return lines


def _v2_interface_run(node, state, step_input):
    template = step_input.get("template_specs", {}).get(node.get("template", ""), {})
    inputs = {}
    for slot, input_cfg in node.get("inputs", {}).items():
        source = _v2_input_source_name(input_cfg)
        rve = state["rve"][source]
        inputs[slot] = {
            "source": f"rve.{source}",
            "source_step": rve.get("source_step"),
            "source_node": rve.get("source_node", source),
            "parameter_txt": node.get("input_parameter_txt_paths", {}).get(slot, ""),
            "variable_prefix": f"rve_{slot}",
        }
    return {
        "node": node["node"],
        "template": node.get("template"),
        "model_path": _v2_model_path(step_input, node),
        "studies": template.get("studies", node.get("studies", {})),
        "inputs": inputs,
        "outputs": {"result_file": node.get("result_file"), "txt_file": node.get("output_txt_path", "")},
    }


def _write_v2_input_parameter_txts(node, state):
    for slot, path in node.get("input_parameter_txt_paths", {}).items():
        source = _v2_input_source_name(node.get("inputs", {}).get(slot, slot))
        write_rve_parameter_txt(path, slot, state["rve"][source])


def _write_v2_output_txt(node, result):
    path = node.get("output_txt_path")
    if not path:
        return
    if node["node"] == "wafer":
        write_wafer_result_txt(path, result)
    else:
        write_rve_result_txt(path, result)


def _v2_model_path(step_input, node):
    if node.get("model_path") or node.get("template_path"):
        return node.get("model_path", node.get("template_path", ""))
    return step_input.get("template_specs", {}).get(node.get("template", ""), {}).get("path", "")


def _v2_input_source_name(input_cfg):
    if isinstance(input_cfg, str):
        return input_cfg
    source = input_cfg.get("source") or input_cfg.get("ref")
    if source and source.startswith("rve."):
        return canonical_ref_name(source)
    if source:
        return source
    raise ValueError(f"Unsupported v2 input: {input_cfg}")


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


def _write_interface_log(output_dir, interface_log):
    dump_data(Path(output_dir) / "logs" / "interface.json", interface_log)


def _write_node_result(output_dir, node, result):
    result_file = node.get("result_file")
    if result_file:
        dump_data(Path(output_dir) / result_file, result)
