from pathlib import Path

from .schemas import StepInput
from .config_io import load_json
from .v2_contract import CANONICAL_NODE_SET, CHAIN_INPUTS, LEGACY_NODE_NAMES, RUN_ORDER, V2_ACTIONS
from .v2_contract import canonical_ref_name
from .rve_txt import write_rve_parameter_txt


def validate_flow(flow):
    if flow.get("version") == 2 or flow.get("layout") == "v2":
        seen = set()
        for step in flow["steps"]:
            step_id = step["id"]
            if step_id in seen:
                raise ValueError(f"Duplicate step id: {step_id}")
            seen.add(step_id)
            _validate_v2_step(step, flow)
        return

    raise ValueError("Only v2 params-txt-driven flow configs are supported")


def _validate_v2_step(step, flow):
    step_id = step["id"]
    nodes = step.get("nodes", {})
    if not isinstance(nodes, dict) or not nodes:
        raise ValueError(f"{step_id} must declare v2 nodes as a mapping")
    templates = _v2_templates(step, flow)
    seen_wafer = False
    for node_id, node in nodes.items():
        if node_id in LEGACY_NODE_NAMES:
            raise ValueError(f"{step_id} uses legacy node name {node_id}")
        if node_id not in CANONICAL_NODE_SET:
            raise ValueError(f"{step_id} has unknown v2 node {node_id}")
        action = node.get("action")
        if action not in V2_ACTIONS:
            raise ValueError(f"{step_id} node {node_id} has unknown action {action}")
        if node_id == "onon" and action == "run":
            raise ValueError(f"{step_id} onon must be alias or inherit, not run")
        if node_id == "wafer":
            seen_wafer = True
            if action != "run":
                raise ValueError(f"{step_id} wafer must run")
        if action == "run":
            template_key = node.get("template")
            if template_key not in templates:
                raise ValueError(f"{step_id} node {node_id} has unknown template {template_key}")
            template = templates[template_key]
            for slot, input_cfg in node.get("inputs", {}).items():
                canonical_ref_name(input_cfg.get("ref"))
                _validate_v2_parameter_contract(step_id, node_id, slot, template)
        elif node.get("inputs"):
            raise ValueError(f"{step_id} node {node_id} with action {action} cannot declare inputs")
    if not seen_wafer:
        raise ValueError(f"{step_id} must declare wafer")


def _validate_v2_parameter_contract(step_id, node_id, slot, template):
    if template.get("input_targets"):
        raise ValueError(f"{step_id} node {node_id} input {slot} uses deprecated input_targets")
    parameter_inputs = template.get("parameter_inputs", {})
    if parameter_inputs and slot not in parameter_inputs:
        raise ValueError(f"{step_id} node {node_id} input {slot} missing parameter_inputs mapping")


def _v2_templates(step, flow):
    registry = step.get("templates") or flow.get("templates") or {}
    return registry.get("templates", registry)


def build_step_input(step, flow, params, state_in_path, output_dir, parameter_txt_paths):
    if step.get("layout_version") == 2 or flow.get("version") == 2 or flow.get("layout") == "v2":
        step_input = _build_v2_step_input(step, flow, params, state_in_path, output_dir, parameter_txt_paths)
        StepInput.from_dict(step_input)
        return step_input

    raise ValueError("Only v2 params-txt-driven step inputs are supported")


def _build_v2_step_input(step, flow, params, state_in_path, output_dir, parameter_txt_paths):
    state = load_json(state_in_path)
    templates = _v2_templates(step, flow)
    node_map = step.get("nodes", {})
    nodes = []
    runs = []
    template_specs = {}
    ordered_names = [name for name in RUN_ORDER if name in node_map]
    if "onon" in node_map:
        ordered_names.append("onon")
    produced = set()
    for node_id in ordered_names:
        node = dict(node_map[node_id])
        entry = {
            "node": node_id,
            "id": node_id,
            "type": node_id,
            "action": node["action"],
            "output": "wafer_result" if node_id == "wafer" else f"rve.{node_id}",
        }
        template_key = node.get("template")
        if template_key:
            template = templates[template_key]
            template_specs[template_key] = template
            entry.update(
                {
                    "template": template_key,
                    "result_file": template.get("outputs", {}).get(
                        "result_file",
                        "wafer_result.json" if node_id == "wafer" else f"{node_id}_rve.json",
                    ),
                }
            )
            if node["action"] == "run":
                entry["inputs"] = _compact_v2_inputs(node_id, node)
                entry["input_parameter_txt_paths"] = _v2_input_parameter_txt_paths(node_id, entry["inputs"], state, output_dir)
                entry["output_txt_path"] = str(_v2_output_txt_path(output_dir, node_id, template))
                runs.append(dict(entry))
        if node["action"] in {"default_from", "alias"}:
            entry["source"] = node.get("source")
        if "merge" in node:
            entry["merge"] = dict(node["merge"])
        nodes.append(entry)
        if node["action"] == "run":
            produced.add(node_id)
    return {
        "step_id": step["id"],
        "step_name": step["name"],
        "process_type": step.get("process_type", "v2"),
        "update_rule": step.get("update_rule", "init"),
        "template_registry": flow.get("template_registry") or step.get("template_registry") or "",
        "template_specs": template_specs,
        "runs": runs,
        "templates": {},
        "nodes": nodes,
        "run_wafer": True,
        "parameters": params,
        "raw_parameters": step.get("raw_parameters", {}),
        "parameter_txt_paths": {key: str(value) for key, value in parameter_txt_paths.items()},
        "parameter_txt_order": step.get("parameter_txt_order", list(parameter_txt_paths)),
        "state_in": str(state_in_path),
        "output_dir": str(output_dir),
    }


def _compact_v2_inputs(node_id, node):
    inputs = {}
    for slot in CHAIN_INPUTS.get(node_id, ()):
        inputs[slot] = slot
    for slot, input_cfg in node.get("inputs", {}).items():
        if isinstance(input_cfg, dict):
            ref = input_cfg.get("ref") or input_cfg.get("source")
            inputs[slot] = canonical_ref_name(ref) if ref else slot
        else:
            inputs[slot] = canonical_ref_name(input_cfg)
    return inputs


def _v2_input_parameter_txt_paths(node_id, inputs, state, output_dir):
    paths = {}
    for slot, source in inputs.items():
        path = Path(output_dir) / "inputs" / f"{node_id}_{slot}.txt"
        paths[slot] = str(path)
        if source in state.get("rve", {}):
            write_rve_parameter_txt(path, slot, state["rve"][source])
    return paths


def _v2_output_txt_path(output_dir, node_id, template):
    txt_file = template.get("outputs", {}).get("txt_file")
    if not txt_file:
        txt_file = "wafer_result.txt" if node_id == "wafer" else f"{node_id}_rve.txt"
    return Path(output_dir) / "outputs" / txt_file
