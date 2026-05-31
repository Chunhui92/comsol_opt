from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


@dataclass(frozen=True)
class RveResult:
    valid: bool
    active: bool
    source_step: str
    source_model: str
    rho: float
    sxx: float
    syy: float
    d: List[List[float]]
    meta: Dict[str, Any]

    @classmethod
    def from_dict(cls, data):
        required = ["valid", "active", "source_step", "source_model", "rho", "stress_eff", "D"]
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(f"RveResult missing keys: {missing}")
        matrix = data["D"]
        if not isinstance(matrix, list) or len(matrix) != 6:
            raise ValueError("RveResult.D must be a 6x6 matrix")
        for row in matrix:
            if not isinstance(row, list) or len(row) != 6:
                raise ValueError("RveResult.D must be a 6x6 matrix")
        stress = data["stress_eff"]
        if "sxx" not in stress or "syy" not in stress:
            raise ValueError("RveResult.stress_eff must contain sxx and syy")
        return cls(
            valid=bool(data["valid"]),
            active=bool(data["active"]),
            source_step=str(data["source_step"]),
            source_model=str(data["source_model"]),
            rho=float(data["rho"]),
            sxx=float(stress["sxx"]),
            syy=float(stress["syy"]),
            d=[[float(value) for value in row] for row in matrix],
            meta=dict(data.get("meta", {})),
        )


@dataclass(frozen=True)
class WaferState:
    step_id: str
    step_name: str
    wafer_result: Dict[str, float]
    device_rves: Dict[str, Dict[str, Any]]
    wafer_inputs: Dict[str, Dict[str, Any]]
    materials_state: Dict[str, Any]
    geometry_state: Dict[str, Any]
    history: List[Dict[str, Any]]

    @classmethod
    def from_dict(cls, data):
        required = [
            "step_id",
            "step_name",
            "wafer_result",
            "device_rves",
            "wafer_inputs",
            "materials_state",
            "geometry_state",
        ]
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(f"WaferState missing keys: {missing}")
        for name, rve in data["device_rves"].items():
            RveResult.from_dict(rve)
        for name, rve in data["wafer_inputs"].items():
            RveResult.from_dict(rve)
        return cls(
            step_id=str(data["step_id"]),
            step_name=str(data["step_name"]),
            wafer_result={key: float(value) for key, value in data["wafer_result"].items()},
            device_rves=dict(data["device_rves"]),
            wafer_inputs=dict(data["wafer_inputs"]),
            materials_state=dict(data["materials_state"]),
            geometry_state=dict(data["geometry_state"]),
            history=list(data.get("history", [])),
        )


@dataclass(frozen=True)
class StepInput:
    step_id: str
    step_name: str
    process_type: str
    update_rule: str
    templates: Dict[str, Any]
    run_devices: List[Dict[str, Any]]
    run_mats: List[str]
    mat_inputs: Dict[str, str]
    run_wafer: bool
    wafer_inputs: Dict[str, List[str]]
    parameters: Dict[str, Any]
    parameter_txt_paths: Dict[str, str]
    state_in: Path
    output_dir: Path

    @classmethod
    def from_dict(cls, data):
        required = [
            "step_id",
            "step_name",
            "process_type",
            "update_rule",
            "templates",
            "run_devices",
            "run_mats",
            "mat_inputs",
            "run_wafer",
            "wafer_inputs",
            "parameters",
            "parameter_txt_paths",
            "state_in",
            "output_dir",
        ]
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(f"StepInput missing keys: {missing}")
        for key in ("struct", "stress", "temp"):
            if key not in data["parameter_txt_paths"]:
                raise ValueError(f"StepInput.parameter_txt_paths missing {key}")
        return cls(
            step_id=str(data["step_id"]),
            step_name=str(data["step_name"]),
            process_type=str(data["process_type"]),
            update_rule=str(data["update_rule"]),
            templates=dict(data["templates"]),
            run_devices=list(data["run_devices"]),
            run_mats=list(data["run_mats"]),
            mat_inputs=dict(data["mat_inputs"]),
            run_wafer=bool(data["run_wafer"]),
            wafer_inputs=dict(data["wafer_inputs"]),
            parameters=dict(data["parameters"]),
            parameter_txt_paths=dict(data["parameter_txt_paths"]),
            state_in=Path(data["state_in"]),
            output_dir=Path(data["output_dir"]),
        )
