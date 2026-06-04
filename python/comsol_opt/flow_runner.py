from pathlib import Path

from .cache_manager import StepCache
from .config_io import dump_data, load_config
from .parameter_txt import ParameterTxtSet
from .parameter_txt import parse_parameter_txt_files
from .params import flatten_params, model_params_from_comsol_txt
from .state import initial_state
from .step_input import build_step_input, validate_flow
from .summary import load_experiment, write_summary


DEFAULT_PARAMETER_MAP_PATH = "archive/legacy_config_scheme/parameter_map.yaml"


def load_flow_definition(flow_path, repo_root):
    flow_path = Path(flow_path)
    repo_root = Path(repo_root)
    flow = load_config(flow_path)
    if "global" not in flow:
        return flow

    global_cfg = flow.get("global", {})
    templates = load_config(repo_root / global_cfg["templates_file"])
    extractors = load_config(repo_root / global_cfg["extractors_file"])
    expanded_steps = []
    for step_ref in flow["steps"]:
        if not step_ref.get("enabled", True):
            continue
        step_cfg = load_config(repo_root / step_ref["config"])
        step_meta = dict(step_cfg["step"])
        txt_files = list(step_cfg.get("params", {}).get("files", []))
        override = global_cfg.get("calibration_override_file")
        if override and override not in txt_files:
            txt_files.append(override)
        txt_paths = [repo_root / item for item in txt_files]
        raw_parameters = parse_parameter_txt_files(txt_paths)
        step = {
            **step_meta,
            "config": step_ref["config"],
            "nodes": [_resolve_node(node, templates, extractors) for node in step_cfg.get("nodes", [])],
            "outputs": step_cfg.get("outputs", {}),
            "parameter_files": txt_files,
            "parameter_txt_order": [Path(item).stem for item in txt_files],
            "raw_parameters": raw_parameters,
            "parameters": model_params_from_comsol_txt(raw_parameters),
            "templates": templates,
            "extractors": extractors,
            "run_wafer": _wafer_runs(step_cfg.get("nodes", [])),
        }
        for key, value in step_ref.items():
            if key not in {"config", "enabled"}:
                step[key] = value
        expanded_steps.append(step)
    flow = dict(flow)
    flow["layout"] = "layered_dag"
    flow["steps"] = expanded_steps
    return flow


def _resolve_node(node, templates, extractors):
    node = dict(node)
    template_key = node.get("template")
    if template_key:
        node["template_path"] = _lookup_template_path(template_key, templates)
    extractor_key = node.get("extractor")
    if extractor_key:
        node["extractor_tags"] = _lookup_extractor_tags(template_key, extractor_key, extractors)
    return node


def _lookup_template_path(template_key, templates):
    section_name, key = template_key.split(".", 1)
    return templates["templates"][section_name][key]["path"]


def _lookup_extractor_tags(template_key, extractor_key, extractors):
    if extractor_key != "default" or not template_key:
        return {}
    template_spec = extractors.get("templates", {}).get(template_key, {})
    default_key = template_spec.get("use_default")
    if default_key:
        return dict(extractors.get("defaults", {}).get(default_key, {}))
    return {}


def _wafer_runs(nodes):
    for node in nodes:
        if node.get("type") == "wafer":
            return node.get("action") == "run"
    return True


def prepare_parameter_txt_set(repo_root, parameter_map_path=None):
    repo_root = Path(repo_root)
    config_path = Path(parameter_map_path) if parameter_map_path else repo_root / DEFAULT_PARAMETER_MAP_PATH
    parameter_map = load_config(config_path)
    return ParameterTxtSet(
        files={key: repo_root / value for key, value in parameter_map["parameter_files"].items()},
        mappings={
            key: {"file": value["file"], "name": value["txt_name"], "unit": value.get("unit", "")}
            for key, value in parameter_map["parameters"].items()
        },
    )


def run_flow(
    flow_path,
    params_path,
    experiment_path,
    backend,
    run_id,
    runs_root,
    repo_root,
    use_cache=False,
    parameter_map_path=None,
    cache_root=None,
):
    flow = load_flow_definition(flow_path, repo_root)
    param_override = load_config(params_path) if params_path is not None else None
    params = _initial_params(param_override, flow)
    validate_flow(flow)
    run_dir = Path(runs_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    initial_path = run_dir / "_initial_state.json"
    dump_data(initial_path, initial_state(params))
    state_in_path = initial_path

    txt_set = None
    if flow.get("layout") != "layered_dag":
        txt_set = prepare_parameter_txt_set(repo_root, parameter_map_path)
    cache = StepCache(cache_root or run_dir / ".cache") if use_cache else None
    completed = 0
    for step in flow["steps"]:
        step_dir = run_dir / step["id"]
        step_dir.mkdir(parents=True, exist_ok=True)
        if "nodes" in step:
            params = param_override or step["parameters"]
            parameter_txt_paths = _copy_layered_parameter_files(repo_root, step, step_dir / "parameters")
        else:
            if txt_set is None:
                raise ValueError("Legacy flow requires a parameter txt set")
            parameter_txt_paths = txt_set.write_trial_files(step_dir / "parameters", flatten_params(params))
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
    if flow.get("layout") == "layered_dag":
        if flow["steps"]:
            return flow["steps"][0]["parameters"]
        return model_params_from_comsol_txt({})
    raise ValueError("params_path is required for legacy flow configs")


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
