from copy import deepcopy
from pathlib import Path

from .cache_manager import StepCache
from .config_io import dump_data, load_config
from .parameter_txt import parse_parameter_txt_files
from .params import model_params_from_comsol_txt
from .state import initial_state_v2
from .step_input import build_step_input, validate_flow
from .summary import load_experiment, write_summary
from .v2_contract import CHAIN_INPUTS, RUN_ORDER


def load_flow_definition(flow_path, repo_root):
    flow_path = Path(flow_path)
    repo_root = Path(repo_root)
    flow = load_config(flow_path)
    if flow.get("version") != 2:
        raise ValueError("Only v2 params-txt-driven flow configs are supported")
    return _load_v2_flow_definition(flow, repo_root)


def _load_v2_flow_definition(flow, repo_root):
    templates_ref = flow.get("templates", {})
    template_registry = templates_ref if isinstance(templates_ref, str) else None
    templates = load_config(repo_root / templates_ref) if isinstance(templates_ref, str) else templates_ref
    templates = _expand_v2_template_families(templates or {})
    parameter_files = list(flow.get("parameter_txt_order", []))
    txt_paths = [repo_root / item for item in parameter_files]
    raw_parameters = parse_parameter_txt_files(txt_paths)
    parameters = model_params_from_comsol_txt(raw_parameters)
    expanded_steps = []
    for step_ref in flow.get("steps", []):
        if isinstance(step_ref, str):
            step_cfg = load_config(repo_root / step_ref)
            config_path = step_ref
            enabled = True
        else:
            config_path = step_ref.get("config")
            step_cfg = load_config(repo_root / config_path) if config_path else dict(step_ref)
            enabled = step_ref.get("enabled", True)
        if not enabled:
            continue
        step = _normalize_v2_step(step_cfg)
        step.update(
            {
                "layout_version": 2,
                "config": config_path,
                "templates": templates,
                "parameter_files": parameter_files,
                "parameter_txt_order": [Path(item).stem for item in parameter_files],
                "raw_parameters": raw_parameters,
                "parameters": parameters,
                "run_wafer": True,
            }
        )
        expanded_steps.append(step)
    flow = dict(flow)
    flow["layout"] = "v2"
    flow["templates"] = templates
    flow["template_registry"] = template_registry
    flow["steps"] = expanded_steps
    return flow


def _normalize_v2_step(step_cfg):
    if "step" in step_cfg:
        step = {**step_cfg["step"], **{key: value for key, value in step_cfg.items() if key != "step"}}
    else:
        step = dict(step_cfg)
    if "id" not in step and "step_id" in step:
        step["id"] = step.pop("step_id")
    if "name" not in step and "step_name" in step:
        step["name"] = step.pop("step_name")
    step.setdefault("process_type", "v2")
    step.setdefault("update_rule", "init")
    step.setdefault("experiment_step", step["id"])
    if "nodes" not in step and ("run" in step or "preset" in step):
        step["nodes"] = _expand_v2_shorthand_nodes(step)
    return step


def _expand_v2_shorthand_nodes(step):
    run = _expand_v2_run_groups(step.get("run") or {}, step.get("preset"))
    merge = step.get("merge") or {}
    default_from = step.get("default_from")
    alias = step.get("alias") or {}
    nodes = {}
    for node in RUN_ORDER:
        if node in run:
            template = run[node]
            if isinstance(template, dict):
                node_cfg = {"action": "run", **template}
            else:
                node_cfg = {"action": "run", "template": template}
            if node in merge:
                node_cfg["merge"] = merge[node]
            inputs = _v2_default_inputs(node)
            if inputs:
                node_cfg["inputs"] = inputs
            nodes[node] = node_cfg
        elif default_from and node not in {"wafer"}:
            nodes[node] = {"action": "default_from", "source": default_from}

    if default_from:
        nodes["onon"] = {"action": "alias", "source": default_from}
    for node, source in alias.items():
        nodes[node] = {"action": "alias", "source": source}

    downstream_templates = step.get("downstream_templates", {})
    if any(name in run for name in ("decap1", "decap2", "decap3", "fecap")) or "die" in run:
        nodes["die"] = {"action": "run", "template": downstream_templates.get("die", "die_model"), "inputs": _v2_default_inputs("die")}
    if any(nodes.get(name, {}).get("action") == "run" for name in ("die", "wafer")):
        nodes["wafer"] = {
            "action": "run",
            "template": run.get("wafer", downstream_templates.get("wafer", "wafer_warp_model")),
            "inputs": _v2_default_inputs("wafer"),
        }
    elif "wafer" in run:
        nodes["wafer"] = {"action": "run", "template": run["wafer"], "inputs": _v2_default_inputs("wafer")}

    return {name: nodes[name] for name in [*RUN_ORDER, "onon"] if name in nodes}


def _expand_v2_run_groups(run, preset=None):
    expanded = {}
    if preset:
        if preset != "full_chain":
            raise ValueError(f"Unsupported v2 preset: {preset}")
        expanded.update(_decap_group_templates("decap_model"))
        expanded["fecap"] = "fecap_model_all"

    run = dict(run)
    decap_group = run.pop("decap", None)
    if decap_group is not None:
        expanded.update(_decap_group_templates(decap_group))
    expanded.update(run)
    return expanded


def _decap_group_templates(group):
    if isinstance(group, dict):
        missing = {"decap1", "decap2", "decap3"} - set(group)
        if missing:
            raise ValueError(f"decap group missing members: {sorted(missing)}")
        return {name: group[name] for name in ("decap1", "decap2", "decap3")}
    return {name: f"{group}_{name}" for name in ("decap1", "decap2", "decap3")}


def _v2_default_inputs(node):
    return {slot: {"ref": f"rve.{source}"} for slot, source in zip(CHAIN_INPUTS.get(node, ()), CHAIN_INPUTS.get(node, ()))}


def _expand_v2_template_families(registry):
    if "template_families" not in registry:
        return registry
    expanded = deepcopy(registry)
    templates = dict(expanded.get("templates", {}))
    for family_name, family in expanded.get("template_families", {}).items():
        base = family.get("base", {})
        for node_type, overrides in family.get("members", {}).items():
            template_key = f"{family_name}_{node_type}"
            spec = _deep_merge_dicts(base, overrides or {})
            spec.setdefault("node_type", node_type)
            spec.setdefault("outputs", {"result_file": f"{node_type}_rve.json"})
            templates[template_key] = spec
    expanded["templates"] = templates
    return expanded


def _deep_merge_dicts(base, override):
    merged = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def run_flow(
    flow_path,
    params_path,
    experiment_path,
    backend,
    run_id,
    runs_root,
    repo_root,
    use_cache=False,
    cache_root=None,
):
    flow = load_flow_definition(flow_path, repo_root)
    param_override = load_config(params_path) if params_path is not None else None
    params = _initial_params(param_override, flow)
    validate_flow(flow)
    run_dir = Path(runs_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    initial_path = run_dir / "_initial_state.json"
    dump_data(initial_path, initial_state_v2(params))
    state_in_path = initial_path

    cache = StepCache(cache_root or run_dir / ".cache") if use_cache else None
    completed = 0
    for step in flow["steps"]:
        step_dir = run_dir / step["id"]
        step_dir.mkdir(parents=True, exist_ok=True)
        params = param_override or step["parameters"]
        parameter_txt_paths = _copy_layered_parameter_files(repo_root, step, step_dir / "parameters")
        step_input = build_step_input(step, flow, params, state_in_path, step_dir, parameter_txt_paths)
        step_input_path = step_dir / "step_input.json"
        dump_data(step_input_path, step_input)
        result = None
        if cache is not None and backend.__class__.__name__ != "DryRunBackend":
            cache_key = cache.key_for(step_input_path)
            if cache.restore(cache_key, step_dir):
                result = {"status": "cache_hit", "step_id": step["id"]}
        if result is None:
            result = backend.run_step(step_input_path)
            if cache is not None and result.get("status") != "dryrun":
                cache.store(cache_key, step_dir)
        if result.get("status") != "dryrun":
            state_in_path = step_dir / "state_out.json"
        completed += 1

    if backend.__class__.__name__ != "DryRunBackend":
        experiment = load_experiment(experiment_path)
        write_summary(run_dir, flow["steps"], experiment)
    return {"run_dir": run_dir, "completed": completed}


def _initial_params(param_override, flow):
    if param_override is not None:
        return param_override
    if flow.get("layout") == "v2":
        if flow["steps"]:
            return flow["steps"][0]["parameters"]
        return model_params_from_comsol_txt({})
    raise ValueError("Only v2 params-txt-driven flow configs are supported")


def _copy_layered_parameter_files(repo_root, step, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    copied = {}
    for item in step["parameter_files"]:
        src = Path(repo_root) / item
        dst = output_dir / src.name
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        copied[src.stem] = dst
    return copied
