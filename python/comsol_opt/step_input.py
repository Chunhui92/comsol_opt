from .schemas import StepInput
from .process import VALID_UPDATE_RULES
from .config_io import load_json
from .v2_contract import CANONICAL_NODE_SET, LEGACY_NODE_NAMES, RUN_ORDER, V2_ACTIONS
from .v2_contract import canonical_ref_name, rve_payload_for_java


VALID_DAG_NODE_TYPES = {"device", "mat", "die", "wafer"}
VALID_DAG_ACTIONS = {"run", "inherit"}
RVE_PREFIX = "rve."


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

    if flow.get("layout") == "layered_dag" or "global" in flow:
        seen = set()
        for step in flow["steps"]:
            if not step.get("enabled", True):
                continue
            step_id = step["id"]
            if step_id in seen:
                raise ValueError(f"Duplicate step id: {step_id}")
            seen.add(step_id)
            if "config" not in step:
                raise ValueError(f"{step_id} missing step config")
            _validate_layered_step(step)
        return

    wafer_slots = set(flow["wafer_slots"])
    device_templates = set(flow["templates"]["devices"])
    mat_templates = set(flow["templates"]["mats"])
    wafer_templates = set(flow["templates"]["wafers"])
    seen = set()
    for step in flow["steps"]:
        step_id = step["id"]
        if step_id in seen:
            raise ValueError(f"Duplicate step id: {step_id}")
        seen.add(step_id)
        if step["update_rule"] not in VALID_UPDATE_RULES:
            raise ValueError(f"{step_id} has unknown update rule {step['update_rule']}")
        if step["wafer_template"] not in wafer_templates:
            raise ValueError(f"{step_id} has unknown wafer template")
        wafer_inputs = step["wafer_inputs"]
        unknown_updates = set(wafer_inputs.get("update", [])) - wafer_slots
        if unknown_updates:
            raise ValueError(f"{step_id} updates unknown wafer slots: {sorted(unknown_updates)}")
        unknown_inherits = set(wafer_inputs.get("inherit", [])) - wafer_slots
        if unknown_inherits:
            raise ValueError(f"{step_id} inherits unknown wafer slots: {sorted(unknown_inherits)}")
        for dev_cfg in step.get("run_devices", []):
            if dev_cfg["template_key"] not in device_templates:
                raise ValueError(f"{step_id} has unknown device template {dev_cfg['template_key']}")
        for mat_name in step.get("run_mats", []):
            if mat_name not in mat_templates:
                raise ValueError(f"{step_id} has unknown mat template {mat_name}")
            if mat_name not in step.get("mat_inputs", {}):
                raise ValueError(f"{step_id} missing mat input for {mat_name}")


def _validate_layered_step(step):
    step_id = step["id"]
    if step["update_rule"] not in VALID_UPDATE_RULES:
        raise ValueError(f"{step_id} has unknown update rule {step['update_rule']}")
    nodes = step.get("nodes", [])
    if not nodes:
        raise ValueError(f"{step_id} has no DAG nodes")

    seen_nodes = set()
    available_rves = set()
    wafer_nodes = 0
    for node in nodes:
        node_id = node.get("id")
        if not node_id:
            raise ValueError(f"{step_id} has a node without id")
        if node_id in seen_nodes:
            raise ValueError(f"{step_id} has duplicate node id {node_id}")
        seen_nodes.add(node_id)

        node_type = node.get("type")
        action = node.get("action")
        if node_type not in VALID_DAG_NODE_TYPES:
            raise ValueError(f"{step_id} node {node_id} has unknown type {node_type}")
        if action not in VALID_DAG_ACTIONS:
            raise ValueError(f"{step_id} node {node_id} has unknown action {action}")

        output = node.get("output")
        expected_output = "wafer_result" if node_type == "wafer" else f"rve.{node_id}"
        if output != expected_output:
            raise ValueError(f"{step_id} node {node_id} output must be {expected_output}")

        if node_type == "wafer":
            wafer_nodes += 1

        if action == "run":
            _validate_run_node(step_id, node, available_rves)
            if not node.get("result_file"):
                raise ValueError(f"{step_id} node {node_id} missing result_file")
        else:
            if node_type == "wafer":
                raise ValueError(f"{step_id} wafer node cannot inherit")
            if node.get("inputs"):
                raise ValueError(f"{step_id} inherited node {node_id} should not declare inputs")

        if output.startswith(RVE_PREFIX):
            available_rves.add(output[len(RVE_PREFIX):])

    if wafer_nodes != 1:
        raise ValueError(f"{step_id} must declare exactly one wafer node")


def _validate_run_node(step_id, node, available_rves):
    node_id = node["id"]
    if not node.get("template"):
        raise ValueError(f"{step_id} node {node_id} missing template")
    if not node.get("template_path"):
        raise ValueError(f"{step_id} node {node_id} has unresolved template {node.get('template')}")
    inputs = node.get("inputs", {})
    if node["type"] == "device":
        if inputs:
            raise ValueError(f"{step_id} device node {node_id} should not declare RVE inputs")
        return

    required_inputs = {
        "mat": {"device"},
        "die": {"mat1", "mat2", "mat3", "mat4"},
        "wafer": {"die", "onon"},
    }[node["type"]]
    missing = required_inputs - set(inputs)
    if missing:
        raise ValueError(f"{step_id} node {node_id} missing inputs: {sorted(missing)}")
    for slot, ref in inputs.items():
        _validate_rve_ref(step_id, node_id, slot, ref, available_rves)


def _validate_rve_ref(step_id, node_id, slot, ref, available_rves):
    if not isinstance(ref, str) or not ref.startswith(RVE_PREFIX):
        raise ValueError(f"{step_id} node {node_id} input {slot} must reference rve.<id>")
    source = ref[len(RVE_PREFIX):]
    if source not in available_rves:
        raise ValueError(f"{step_id} node {node_id} input {slot} references unavailable {ref}")


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
                target = input_cfg.get("target") or template.get("input_targets", {}).get(slot)
                if target:
                    _validate_v2_target(step_id, node_id, slot, target)
        elif node.get("inputs"):
            raise ValueError(f"{step_id} node {node_id} with action {action} cannot declare inputs")
    if not seen_wafer:
        raise ValueError(f"{step_id} must declare wafer")


def _validate_v2_target(step_id, node_id, slot, target):
    material = target.get("material", {})
    stress = target.get("stress", {})
    for key in ("component", "tag", "elastic_group", "elastic_property", "elasticity_order", "D_format"):
        if not material.get(key):
            raise ValueError(f"{step_id} node {node_id} input {slot} missing material.{key}")
    if material["D_format"] != "symmetric_upper21":
        raise ValueError(f"{step_id} node {node_id} input {slot} must use symmetric_upper21 D")
    for key in ("component", "physics", "feature", "property"):
        if not stress.get(key):
            raise ValueError(f"{step_id} node {node_id} input {slot} missing stress.{key}")


def _v2_templates(step, flow):
    registry = step.get("templates") or flow.get("templates") or {}
    return registry.get("templates", registry)


def build_step_input(step, flow, params, state_in_path, output_dir, parameter_txt_paths):
    if step.get("layout_version") == 2 or flow.get("version") == 2 or flow.get("layout") == "v2":
        step_input = _build_v2_step_input(step, flow, params, state_in_path, output_dir, parameter_txt_paths)
        StepInput.from_dict(step_input)
        return step_input

    if "nodes" in step:
        step_input = {
            "step_id": step["id"],
            "step_name": step["name"],
            "process_type": step["process_type"],
            "update_rule": step["update_rule"],
            "templates": step.get("templates", {}),
            "extractors": step.get("extractors", {}),
            "nodes": step["nodes"],
            "run_wafer": step.get("run_wafer", True),
            "parameters": params,
            "raw_parameters": step.get("raw_parameters", {}),
            "parameter_txt_paths": {key: str(value) for key, value in parameter_txt_paths.items()},
            "parameter_txt_order": step.get("parameter_txt_order", list(parameter_txt_paths)),
            "state_in": str(state_in_path),
            "output_dir": str(output_dir),
        }
        StepInput.from_dict(step_input)
        return step_input

    device_templates = flow["templates"]["devices"]
    wafer_inputs = normalize_wafer_inputs(step, flow["wafer_slots"])
    step_input = {
        "step_id": step["id"],
        "step_name": step["name"],
        "process_type": step["process_type"],
        "update_rule": step["update_rule"],
        "templates": {
            "devices": {
                dev["name"]: device_templates[dev["template_key"]]
                for dev in step.get("run_devices", [])
            },
            "mats": {mat: flow["templates"]["mats"][mat] for mat in flow["templates"]["mats"]},
            "wafer": flow["templates"]["wafers"][step["wafer_template"]],
        },
        "run_devices": step.get("run_devices", []),
        "run_mats": step.get("run_mats", []),
        "mat_inputs": step.get("mat_inputs", {}),
        "run_wafer": step.get("run_wafer", True),
        "wafer_inputs": wafer_inputs,
        "parameters": params,
        "parameter_txt_paths": {key: str(value) for key, value in parameter_txt_paths.items()},
        "state_in": str(state_in_path),
        "output_dir": str(output_dir),
    }
    StepInput.from_dict(step_input)
    return step_input


def _build_v2_step_input(step, flow, params, state_in_path, output_dir, parameter_txt_paths):
    state = load_json(state_in_path)
    templates = _v2_templates(step, flow)
    node_map = step.get("nodes", {})
    nodes = []
    ordered_names = [name for name in RUN_ORDER if name in node_map]
    if "onon" in node_map:
        ordered_names.append("onon")
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
            entry.update(
                {
                    "template": template_key,
                    "template_path": template.get("path", ""),
                    "model_path": template.get("path", ""),
                    "studies": dict(template.get("studies", {})),
                    "extractors": dict(template.get("extractors", {})),
                    "input_targets": dict(template.get("input_targets", {})),
                    "result_file": template.get("outputs", {}).get(
                        "result_file",
                        "wafer_result.json" if node_id == "wafer" else f"{node_id}_rve.json",
                    ),
                }
            )
            if node["action"] == "run":
                entry["inputs"] = _expand_v2_inputs(node, template, state)
        if node["action"] in {"default_from", "alias"}:
            entry["source"] = node.get("source")
        if "merge" in node:
            entry["merge"] = dict(node["merge"])
        nodes.append(entry)
    return {
        "step_id": step["id"],
        "step_name": step["name"],
        "process_type": step.get("process_type", "v2"),
        "update_rule": step.get("update_rule", "init"),
        "templates": step.get("templates") or flow.get("templates", {}),
        "nodes": nodes,
        "run_wafer": True,
        "parameters": params,
        "raw_parameters": step.get("raw_parameters", {}),
        "parameter_txt_paths": {key: str(value) for key, value in parameter_txt_paths.items()},
        "parameter_txt_order": step.get("parameter_txt_order", list(parameter_txt_paths)),
        "state_in": str(state_in_path),
        "output_dir": str(output_dir),
    }


def _expand_v2_inputs(node, template, state):
    expanded = {}
    for slot, input_cfg in node.get("inputs", {}).items():
        ref = input_cfg["ref"]
        source = canonical_ref_name(ref)
        rve = state["rve"][source]
        target = input_cfg.get("target") or template.get("input_targets", {}).get(slot, {})
        expanded[slot] = {"source": ref, "rve": rve_payload_for_java(rve), "target": target}
    return expanded


def normalize_wafer_inputs(step, wafer_slots):
    update = list(step["wafer_inputs"].get("update", []))
    inherit = step["wafer_inputs"].get("inherit")
    if inherit is None:
        update_set = set(update)
        inherit = [slot for slot in wafer_slots if slot not in update_set]
    return {"update": update, "inherit": list(inherit)}
