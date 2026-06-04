from .schemas import StepInput
from .process import VALID_UPDATE_RULES


VALID_DAG_NODE_TYPES = {"device", "mat", "die", "wafer"}
VALID_DAG_ACTIONS = {"run", "inherit"}
RVE_PREFIX = "rve."


def validate_flow(flow):
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


def build_step_input(step, flow, params, state_in_path, output_dir, parameter_txt_paths):
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


def normalize_wafer_inputs(step, wafer_slots):
    update = list(step["wafer_inputs"].get("update", []))
    inherit = step["wafer_inputs"].get("inherit")
    if inherit is None:
        update_set = set(update)
        inherit = [slot for slot in wafer_slots if slot not in update_set]
    return {"update": update, "inherit": list(inherit)}
