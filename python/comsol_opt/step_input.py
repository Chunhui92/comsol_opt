from .schemas import StepInput
from .process import VALID_UPDATE_RULES


def validate_flow(flow):
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


def build_step_input(step, flow, params, state_in_path, output_dir, parameter_txt_paths):
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
