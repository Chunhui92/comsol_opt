import csv
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "python"
sys.path.insert(0, str(PYTHON_DIR))

from comsol_opt.config_io import dump_data, load_config, load_json
from comsol_opt.cache_manager import StepCache
from comsol_opt.flow import prepare_parameter_txt_set, run_flow
from comsol_opt.calibration import active_calibration_space, parameter_regularization_loss, select_groups
from comsol_opt.loss import compute_loss_from_rows
from comsol_opt.backends import make_backend
from comsol_opt.parameter_txt import ParameterTxtSet
from comsol_opt.schemas import RveResult, StepInput, WaferState


class V2ContractTests(unittest.TestCase):
    def test_upper21_round_trips_symmetric_matrix_and_defines_canonical_nodes(self):
        from comsol_opt.v2_contract import CANONICAL_NODES, matrix_to_upper21, upper21_to_matrix

        matrix = [[float(10 * row + col) for col in range(6)] for row in range(6)]
        for row in range(6):
            for col in range(row):
                matrix[row][col] = matrix[col][row]

        upper = matrix_to_upper21(matrix)

        self.assertEqual(len(upper), 21)
        self.assertEqual(upper[0], matrix[0][0])
        self.assertEqual(upper[1], matrix[0][1])
        self.assertEqual(upper[-1], matrix[5][5])
        self.assertEqual(upper21_to_matrix(upper), matrix)
        self.assertEqual(
            CANONICAL_NODES,
            ("pillar", "sc", "decap1", "decap2", "decap3", "fecap", "die", "wafer", "onon"),
        )


class V2FlowTests(unittest.TestCase):
    def test_v2_validation_rejects_legacy_names_and_onon_run(self):
        from comsol_opt.step_input import validate_flow

        legacy_flow = {"version": 2, "steps": [{"id": "S04", "name": "bad", "nodes": {"mat1": {"action": "inherit"}}}]}
        with self.assertRaises(ValueError):
            validate_flow(legacy_flow)

        onon_flow = {"version": 2, "steps": [{"id": "S02", "name": "bad", "nodes": {"onon": {"action": "run"}}}]}
        with self.assertRaises(ValueError):
            validate_flow(onon_flow)

    def test_v2_step_input_expands_rve_payload_and_material_target(self):
        from comsol_opt.step_input import build_step_input, validate_flow

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "state_in.json"
            params_path = root / "global.txt"
            params_path.write_text("x 1\n", encoding="utf-8")
            state = {
                "step_id": "S03",
                "step_name": "previous",
                "wafer_result": {"bow_x_um": 0.0, "bow_y_um": 0.0, "kx": 0.0, "ky": 0.0},
                "rve": {name: valid_rve_dict() for name in ("pillar", "sc", "decap1", "decap2", "decap3", "fecap", "die", "onon")},
                "materials_state": {},
                "geometry_state": {},
                "history": [],
            }
            dump_data(state_path, state)
            flow, step = minimal_v2_flow_and_step()
            validate_flow(flow)

            step_input = build_step_input(
                step,
                flow,
                params={},
                state_in_path=state_path,
                output_dir=root / "S04_sc_etch",
                parameter_txt_paths={"global": params_path},
            )

            fecap = next(node for node in step_input["nodes"] if node["node"] == "fecap")
            pillar_input = fecap["inputs"]["pillar"]
            self.assertEqual(pillar_input["source"], "rve.pillar")
            self.assertEqual(pillar_input["target"]["material"]["elastic_property"], "D")
            self.assertEqual(pillar_input["target"]["stress"]["property"], "S0")
            self.assertEqual(len(pillar_input["rve"]["D_upper21"]), 21)
            self.assertEqual(pillar_input["rve"]["D_format"], "symmetric_upper21")

    def test_v2_flow_definition_loads_step_files_and_templates(self):
        from comsol_opt.flow_runner import load_flow_definition

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs" / "steps").mkdir(parents=True)
            (root / "configs" / "params").mkdir(parents=True)
            (root / "configs" / "params" / "global.txt").write_text("sigma_O_base 100[MPa]\n", encoding="utf-8")
            dump_data(root / "configs" / "templates.yaml", minimal_v2_flow_and_step()[0]["templates"])
            dump_data(root / "configs" / "steps" / "S04_sc_etch.yaml", minimal_v2_flow_and_step()[1])
            dump_data(
                root / "configs" / "flow.yaml",
                {
                    "version": 2,
                    "run_id_default": "dev_v2",
                    "templates": "configs/templates.yaml",
                    "parameter_txt_order": ["configs/params/global.txt"],
                    "steps": ["configs/steps/S04_sc_etch.yaml"],
                },
            )

            flow = load_flow_definition(root / "configs" / "flow.yaml", root)

            self.assertEqual(flow["layout"], "v2")
            self.assertEqual(flow["steps"][0]["id"], "S04")
            self.assertEqual(flow["steps"][0]["layout_version"], 2)
            self.assertIn("fecap_model_all", flow["steps"][0]["templates"]["templates"])


class V2MockFlowTests(unittest.TestCase):
    def test_v2_mock_initializes_alias_defaults_and_uses_current_step_inputs(self):
        from comsol_opt.mock_comsol_worker import run_mock_step
        from comsol_opt.step_input import build_step_input

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            params_path = root / "global.txt"
            params_path.write_text("x 1\n", encoding="utf-8")
            initial_state = {
                "step_id": "INIT",
                "step_name": "initial",
                "wafer_result": {"bow_x_um": 0.0, "bow_y_um": 0.0, "kx": 0.0, "ky": 0.0},
                "rve": {name: valid_rve_dict() for name in ("pillar", "sc", "decap1", "decap2", "decap3", "fecap", "die", "onon")},
                "materials_state": {"FEOL": {}},
                "geometry_state": {},
                "history": [],
            }
            initial_path = root / "initial.json"
            dump_data(initial_path, initial_state)

            flow, s02 = v2_s02_flow_and_step()
            s02_dir = root / "S02_onon_dep"
            s02_input = build_step_input(s02, flow, {}, initial_path, s02_dir, {"global": params_path})
            s02_input_path = s02_dir / "step_input.json"
            dump_data(s02_input_path, s02_input)
            run_mock_step(s02_input_path)
            s02_state = load_json(s02_dir / "state_out.json")
            self.assertEqual(s02_state["rve"]["onon"]["status"], "alias")
            self.assertEqual(s02_state["rve"]["onon"]["source_node"], "pillar")
            self.assertEqual(s02_state["rve"]["decap1"]["status"], "default_from")
            self.assertFalse(s02_state["wafer_result"].get("wafer_skipped", False))

            flow, s04 = minimal_v2_flow_and_step()
            s04_dir = root / "S04_sc_etch"
            s04_input = build_step_input(s04, flow, {}, s02_dir / "state_out.json", s04_dir, {"global": params_path})
            s04_input_path = s04_dir / "step_input.json"
            dump_data(s04_input_path, s04_input)
            run_mock_step(s04_input_path)
            manifest = load_json(s04_dir / "manifest.json")
            self.assertEqual(manifest["nodes"]["fecap"]["inputs"]["sc"]["source_step"], "S04")
            log_text = (s04_dir / "logs" / "step.log").read_text(encoding="utf-8")
            self.assertIn("step_id=S04 step_name=sc_etch", log_text)
            self.assertIn("node=sc action=run template=sc_model", log_text)
            self.assertIn("node=fecap action=run template=fecap_model_all", log_text)
            self.assertIn("input slot=sc source=rve.sc source_step=S04", log_text)
            self.assertIn("material component=comp8 tag=mat_pillar_eff", log_text)
            self.assertIn("stress type=initial_stress component=comp8 physics=solid8", log_text)
            self.assertIn("wafer bow_x_um=", log_text)

    def test_v2_mock_stress_only_node_inherits_previous_D(self):
        from comsol_opt.mock_comsol_worker import run_mock_step
        from comsol_opt.step_input import build_step_input

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            params_path = root / "global.txt"
            params_path.write_text("x 1\n", encoding="utf-8")
            flow, s16 = v2_s16_flow_and_step()
            previous = {
                "step_id": "S15",
                "step_name": "previous",
                "wafer_result": {"bow_x_um": 0.0, "bow_y_um": 0.0, "kx": 0.0, "ky": 0.0},
                "rve": {name: valid_rve_dict() for name in ("pillar", "sc", "decap1", "decap2", "decap3", "fecap", "die", "onon")},
                "materials_state": {"FEOL": {}},
                "geometry_state": {},
                "history": [],
            }
            previous["rve"]["fecap"]["D"][0][0] = 123.0
            previous_path = root / "previous.json"
            dump_data(previous_path, previous)
            s16_dir = root / "S16_feslit_etch"
            s16_input = build_step_input(s16, flow, {}, previous_path, s16_dir, {"global": params_path})
            s16_input_path = s16_dir / "step_input.json"
            dump_data(s16_input_path, s16_input)

            run_mock_step(s16_input_path)

            state = load_json(s16_dir / "state_out.json")
            self.assertEqual(state["rve"]["fecap"]["status"], "run_stress_only")
            self.assertEqual(state["rve"]["fecap"]["D"][0][0], 123.0)

    def test_v2_summary_uses_canonical_fields(self):
        from comsol_opt.summary import write_summary

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            step_dir = run_dir / "S04"
            flow, step = minimal_v2_flow_and_step()
            state = {
                "step_id": "S04",
                "step_name": "sc_etch",
                "wafer_result": {"bow_x_um": 1.0, "bow_y_um": 2.0, "kx": 0.0, "ky": 0.0},
                "rve": {name: {**valid_rve_dict(), "status": "inherit"} for name in ("pillar", "sc", "decap1", "decap2", "decap3", "fecap", "die", "onon")},
                "materials_state": {},
                "geometry_state": {},
                "history": [],
            }
            state["rve"]["onon"]["source_node"] = "pillar"
            dump_data(step_dir / "state_out.json", state)

            summary_path = write_summary(
                run_dir,
                [step],
                experiment={"S04": {"bow_x_um": "1.5", "bow_y_um": "2.5", "weight_x": "2.0", "weight_y": "3.0"}},
            )
            with summary_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(rows[0]["decap1_status"], "inherit")
            self.assertEqual(rows[0]["fecap_status"], "inherit")
            self.assertEqual(rows[0]["onon_source_node"], "pillar")
            self.assertEqual(rows[0]["bow_x_um"], "1.0")
            self.assertEqual(rows[0]["bow_x_exp_um"], "1.5")
            self.assertEqual(rows[0]["weight_x"], "2.0")
            self.assertNotIn("bow_x_sim_um", rows[0])

    def test_loss_accepts_v2_canonical_summary_rows(self):
        rows = [{"bow_x_um": "4.0", "bow_y_um": "8.0", "bow_x_exp_um": "1.0", "bow_y_exp_um": "2.0"}]
        loss = compute_loss_from_rows(rows, scale_x=3.0, scale_y=3.0)
        self.assertEqual(loss["n_steps"], 1)
        self.assertEqual(loss["loss"], 5.0)

    def test_comsol_backend_writes_step_log_around_worker_dispatch(self):
        from comsol_opt.backends import ComsolBackend

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = root / "worker.py"
            worker.write_text(
                "import json, pathlib, sys\n"
                "step_input=json.loads(pathlib.Path(sys.argv[1]).read_text())\n"
                "out=pathlib.Path(step_input['output_dir'])\n"
                "out.mkdir(parents=True, exist_ok=True)\n"
                "(out/'step_result.json').write_text(json.dumps({'status':'success','step_id':step_input['step_id']}))\n",
                encoding="utf-8",
            )
            state = root / "state.json"
            dump_data(state, {"step_id": "INIT", "step_name": "initial", "wafer_result": {}, "rve": {}, "materials_state": {}, "geometry_state": {}, "history": []})
            step_input = root / "S04" / "step_input.json"
            dump_data(
                step_input,
                {
                    "step_id": "S04",
                    "step_name": "sc_etch",
                    "output_dir": str(root / "S04"),
                    "state_in": str(state),
                },
            )

            result = ComsolBackend([sys.executable, str(worker)]).run_step(step_input)

            self.assertEqual(result["status"], "success")
            log_text = (root / "S04" / "logs" / "step.log").read_text(encoding="utf-8")
            self.assertIn("backend=comsol step_id=S04 step_name=sc_etch", log_text)
            self.assertIn("command=", log_text)
            self.assertIn("worker_status=success", log_text)


class ParameterTxtSetTests(unittest.TestCase):
    def test_updates_struct_stress_and_temp_parameter_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            struct = root / "struct.txt"
            stress = root / "stress.txt"
            temp = root / "temp.txt"
            struct.write_text("pillar_diameter\t120e-9[m]\tpillar diameter\n", encoding="utf-8")
            stress.write_text(
                "sigma_feol_x\t180e6[Pa]\tFEOL x stress\nsigma_w_fill\t500e6[Pa]\tW fill stress\n",
                encoding="utf-8",
            )
            temp.write_text("process_temp\t25[degC]\tprocess temperature\n", encoding="utf-8")

            txt_set = ParameterTxtSet(
                files={"struct": struct, "stress": stress, "temp": temp},
                mappings={
                    "geometry.pillar_diameter": {"file": "struct", "name": "pillar_diameter", "unit": "m"},
                    "FEOL.sigma_init_x": {"file": "stress", "name": "sigma_feol_x", "unit": "Pa"},
                    "W.sigma_fill": {"file": "stress", "name": "sigma_w_fill", "unit": "Pa"},
                    "process.temperature_C": {"file": "temp", "name": "process_temp", "unit": "degC"},
                    "missing.param": {"file": "stress", "name": "missing_param", "unit": "Pa"},
                },
            )

            output = root / "trial_params"
            paths = txt_set.write_trial_files(
                output,
                {
                    "geometry.pillar_diameter": 125e-9,
                    "FEOL.sigma_init_x": 175e6,
                    "W.sigma_fill": 600e6,
                    "process.temperature_C": 300,
                    "missing.param": 42,
                },
            )

            self.assertEqual(set(paths), {"struct", "stress", "temp"})
            self.assertIn("pillar_diameter\t1.25e-07[m]\tpillar diameter", paths["struct"].read_text(encoding="utf-8"))
            stress_text = paths["stress"].read_text(encoding="utf-8")
            self.assertIn("sigma_feol_x\t175000000.0[Pa]\tFEOL x stress", stress_text)
            self.assertIn("sigma_w_fill\t600000000.0[Pa]\tW fill stress", stress_text)
            self.assertIn("missing_param\t42[Pa]\tgenerated by comsol_opt", stress_text)
            self.assertIn("process_temp\t300[degC]\tprocess temperature", paths["temp"].read_text(encoding="utf-8"))


class WorkflowIntegrationTests(unittest.TestCase):
    def test_default_v2_mock_flow_writes_step_logs_and_canonical_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            cmd = [
                sys.executable,
                str(ROOT / "python" / "run_flow.py"),
                "--backend",
                "mock",
                "--run-id",
                "v2_mock_test",
                "--runs-root",
                str(run_root),
            ]
            completed = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=True)
            self.assertIn("Completed 20 steps", completed.stdout)

            run_dir = run_root / "v2_mock_test"
            self.assertTrue((run_dir / "S20" / "logs" / "step.log").exists())
            log_text = (run_dir / "S20" / "logs" / "step.log").read_text(encoding="utf-8")
            self.assertIn("step_id=S20 step_name=feslit_buff_cmp", log_text)
            self.assertIn("node=fecap action=run", log_text)
            self.assertIn("material component=", log_text)
            with (run_dir / "summary.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 20)
            self.assertIn("decap1_status", rows[0])
            self.assertIn("fecap_status", rows[0])
            self.assertIn("bow_x_um", rows[0])
            self.assertNotIn("mat1_status", rows[0])

    def test_mock_flow_preserves_rve_inheritance_and_asi_template_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            cmd = [
                sys.executable,
                str(ROOT / "python" / "run_flow.py"),
                "--backend",
                "mock",
                "--run-id",
                "mock_test",
                "--runs-root",
                str(run_root),
            ]
            completed = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=True)
            self.assertIn("Completed 20 steps", completed.stdout)

            run_dir = run_root / "mock_test"
            s02 = json.loads((run_dir / "S02" / "state_out.json").read_text(encoding="utf-8"))
            self.assertEqual(s02["rve"]["onon"]["status"], "alias")
            self.assertEqual(s02["rve"]["decap2"]["status"], "default_from")
            self.assertEqual(s02["rve"]["fecap"]["status"], "default_from")
            self.assertEqual(s02["rve"]["die"]["source_step"], "S02")
            self.assertEqual(set(s02["rve"]), {"pillar", "sc", "decap1", "decap2", "decap3", "fecap", "die", "onon"})

            s02_input = json.loads((run_dir / "S02" / "step_input.json").read_text(encoding="utf-8"))
            self.assertEqual([node["node"] for node in s02_input["nodes"][:3]], ["pillar", "sc", "decap1"])
            self.assertEqual(
                s02_input["parameter_txt_order"],
                ["global_params", "calibration_override"],
            )

            manifest = json.loads((run_dir / "S02" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["nodes"]["onon"]["action"], "alias")
            self.assertEqual(manifest["nodes"]["onon"]["source_node"], "pillar")
            self.assertTrue((run_dir / "S02" / "pillar_rve.json").exists())
            self.assertTrue((run_dir / "S02" / "wafer_result.json").exists())

            with (run_dir / "summary.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 20)
            loss = compute_loss_from_rows(rows, scale_x=30.0, scale_y=30.0)
            self.assertGreater(loss["loss"], 0.0)

    def test_layered_flow_uses_each_step_parameters_without_global_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / "configs", root / "configs")
            global_params = root / "configs" / "params" / "global_params.txt"
            global_params.write_text(
                global_params.read_text(encoding="utf-8").replace("r_ONON_trench           0.75", "r_ONON_trench           0.42"),
                encoding="utf-8",
            )
            result = run_flow(
                flow_path=root / "configs" / "flow.yaml",
                params_path=None,
                experiment_path=ROOT / "configs" / "experiments" / "bow_experiment.csv",
                backend=make_backend("dryrun"),
                run_id="step_params",
                runs_root=root / "runs",
                repo_root=root,
            )
            s02_input = load_json(result["run_dir"] / "S02" / "step_input.json")
            self.assertEqual(s02_input["parameters"]["release"]["trench_etch_ONON"], 0.42)

    def test_step_cache_key_includes_parameter_txt_file_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            params = root / "params.txt"
            state = root / "state.json"
            step_input = root / "step_input.json"
            params.write_text("x 1 first\n", encoding="utf-8")
            dump_data(state, {"wafer_result": {}, "rve": {}, "materials_state": {}, "geometry_state": {}, "history": []})
            dump_data(
                step_input,
                {
                    "step_id": "S00",
                    "templates": {},
                    "nodes": [],
                    "parameters": {},
                    "parameter_txt_paths": {"params": str(params)},
                    "parameter_txt_order": ["params"],
                    "state_in": str(state),
                },
            )
            cache = StepCache(root / ".cache")
            first_key = cache.key_for(step_input)
            params.write_text("x 2 changed\n", encoding="utf-8")
            self.assertNotEqual(first_key, cache.key_for(step_input))

    def test_dryrun_generates_step_inputs_without_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            cmd = [
                sys.executable,
                str(ROOT / "python" / "run_flow.py"),
                "--backend",
                "dryrun",
                "--run-id",
                "dryrun_test",
                "--runs-root",
                str(run_root),
            ]
            subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=True)
            run_dir = run_root / "dryrun_test"
            self.assertTrue((run_dir / "S01" / "step_input.json").exists())
            self.assertFalse((run_dir / "S01" / "state_out.json").exists())

    def test_mock_calibration_writes_history_and_best_params_without_optuna(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            cmd = [
                sys.executable,
                str(ROOT / "python" / "calibration_optuna.py"),
                "--backend",
                "mock",
                "--run-id",
                "calib_test",
                "--runs-root",
                str(run_root),
                "--n-trials",
                "2",
            ]
            completed = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=True)
            self.assertIn("best_loss=", completed.stdout)
            calib_dir = run_root / "calib_test"
            self.assertTrue((calib_dir / "calibration_history.csv").exists())
            self.assertTrue((calib_dir / "best_params.yaml").exists())
            self.assertTrue((calib_dir / "best_summary.csv").exists())

    def test_staged_calibration_writes_per_stage_outputs_and_carries_params(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            cmd = [
                sys.executable,
                str(ROOT / "python" / "calibration_optuna.py"),
                "--backend",
                "mock",
                "--run-id",
                "staged_test",
                "--runs-root",
                str(run_root),
                "--n-trials",
                "2",
                "--optimizer",
                "random",
                "--mode",
                "staged",
                "--stages",
                "G2_trench_release,G10_final",
            ]
            completed = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=True)
            self.assertIn("completed_stages=2", completed.stdout)

            calib_dir = run_root / "staged_test"
            g0 = calib_dir / "out" / "S02_G2_trench_release"
            g1 = calib_dir / "out" / "S20_G10_final"
            self.assertTrue((g0 / "stage.log").exists())
            self.assertTrue((g0 / "best_params.yaml").exists())
            self.assertTrue((g0 / "best_summary.csv").exists())
            self.assertTrue((g1 / "stage.log").exists())
            self.assertTrue((g1 / "best_params.yaml").exists())
            self.assertTrue((calib_dir / "out" / "final_params.yaml").exists())

            g0_params = load_config(g0 / "best_params.yaml")
            g1_trial_params = load_config(g1 / "trials" / "trial_0000" / "params_trial.yaml")
            self.assertEqual(g1_trial_params["FEOL"], g0_params["FEOL"])

            g1_log = (g1 / "stage.log").read_text(encoding="utf-8")
            self.assertIn("stage=G10_final", g1_log)
            self.assertIn("params=release.final_ONON", g1_log)
            self.assertTrue((g1 / ".cache").exists())
            self.assertFalse((g1 / "trials" / "trial_0000" / "flow" / ".cache").exists())

    def test_mock_flow_can_skip_wafer_solve_and_preserve_previous_result(self):
        self.skipTest("v2 requires wafer to run every step")

    def test_mock_flow_rejects_unknown_process_update_rule(self):
        self.skipTest("v2 currently treats process updates as COMSOL template behavior")


class ConfigTests(unittest.TestCase):
    def test_default_flow_config_loads_as_yaml(self):
        flow = load_config(ROOT / "configs" / "flow.yaml")
        self.assertEqual(flow["version"], 2)
        self.assertEqual(len(flow["steps"]), 20)
        self.assertEqual(flow["steps"][0], "configs/steps/S01_w_plug_cmp.yaml")
        self.assertEqual(flow["steps"][-1], "configs/steps/S20_feslit_buff_cmp.yaml")

    def test_configs_directory_contains_only_active_layered_scheme(self):
        active_root_files = {
            "calibration_space.yaml",
            "extractors.yaml",
            "flow.yaml",
            "templates.yaml",
        }
        actual_root_files = {
            path.name
            for path in (ROOT / "configs").iterdir()
            if path.is_file() and not path.name.startswith(".")
        }
        self.assertEqual(actual_root_files, active_root_files)
        self.assertFalse((ROOT / "params").exists())
        for legacy_name in ("params_nominal.yaml", "parameter_map.yaml", "template_tags.yaml"):
            self.assertTrue((ROOT / "archive" / "legacy_config_scheme" / legacy_name).exists())

    def test_config_files_are_real_yaml_and_flow_is_readable(self):
        for path in (ROOT / "configs").glob("*.yaml"):
            text = path.read_text(encoding="utf-8")
            self.assertFalse(text.lstrip().startswith("{"), path.name)
        self.assertTrue(all(len(line) <= 120 for line in (ROOT / "configs" / "flow.yaml").read_text(encoding="utf-8").splitlines()))

    def test_default_v2_flow_expands_twenty_step_dag(self):
        from comsol_opt.flow_runner import load_flow_definition

        flow = load_flow_definition(ROOT / "configs" / "flow.yaml", ROOT)
        self.assertEqual(flow["layout"], "v2")
        self.assertEqual([step["id"] for step in flow["steps"][:3]], ["S01", "S02", "S03"])
        self.assertEqual(flow["steps"][-1]["id"], "S20")
        self.assertEqual(flow["steps"][1]["nodes"]["onon"]["action"], "alias")
        self.assertNotIn("mat1", json.dumps(flow["steps"]))

    def test_default_paths_are_flattened(self):
        flow = load_config(ROOT / "configs" / "templates.yaml")
        template_paths = [spec["path"] for spec in flow["templates"].values()]
        self.assertTrue(template_paths)
        self.assertTrue(all(path.startswith("models/process_models/") for path in template_paths))

    def test_legacy_parameter_map_is_archived_but_still_supported(self):
        parameter_map = load_config(ROOT / "archive" / "legacy_config_scheme" / "parameter_map.yaml")
        txt_set = prepare_parameter_txt_set(ROOT)
        mapping = parameter_map["parameters"]["W.sigma_fill"]
        self.assertEqual(mapping["file"], "stress")
        self.assertEqual(mapping["txt_name"], "sigma_w_fill")
        self.assertEqual(txt_set.mappings["W.sigma_fill"]["file"], "stress")
        self.assertEqual(txt_set.mappings["W.sigma_fill"]["name"], "sigma_w_fill")

    def test_cli_defaults_do_not_point_to_legacy_config_files(self):
        run_flow_text = (ROOT / "python" / "run_flow.py").read_text(encoding="utf-8")
        calibration_text = (ROOT / "python" / "calibration_optuna.py").read_text(encoding="utf-8")
        self.assertNotIn("params_nominal.yaml", run_flow_text)
        self.assertNotIn("parameter_map.yaml", run_flow_text)
        self.assertNotIn("exp\" / \"bow_experiment.csv", run_flow_text)
        self.assertNotIn("params_nominal.yaml", calibration_text)
        self.assertIn("configs\" / \"experiments\" / \"bow_experiment.csv", run_flow_text)

    def test_calibration_space_has_prior_scale_and_units(self):
        space = load_config(ROOT / "configs" / "calibration_space.yaml")
        missing = []
        for group in space["groups"]:
            for spec in group["params"]:
                for key in ("prior", "scale", "unit"):
                    if key not in spec:
                        missing.append((group["name"], spec["key"], key))
        self.assertEqual(missing, [])

    def test_calibration_defaults_target_only_enabled_flow_steps(self):
        space = load_config(ROOT / "configs" / "calibration_space.yaml")
        active = active_calibration_space(space, ROOT / "configs" / "flow.yaml", ROOT)
        self.assertIn("G1_ONON_base", [group["name"] for group in active["groups"]])
        self.assertIn("G10_final", [group["name"] for group in active["groups"]])
        groups = select_groups(space, None, ROOT / "configs" / "flow.yaml", ROOT)
        self.assertEqual([group["name"] for group in groups], [group["name"] for group in active["groups"]])
        with self.assertRaises(ValueError):
            select_groups(space, "G0_init", ROOT / "configs" / "flow.yaml", ROOT)

    def test_layered_flow_validation_rejects_bad_node_references(self):
        from comsol_opt.flow_runner import load_flow_definition
        from comsol_opt.step_input import validate_flow

        flow = load_flow_definition(ROOT / "configs" / "flow.yaml", ROOT)
        flow["steps"][3]["nodes"]["fecap"]["inputs"]["sc"]["ref"] = "rve.missing_sc"
        with self.assertRaises(ValueError):
            validate_flow(flow)

    def test_prior_scale_regularization_uses_normalized_distance(self):
        specs = [
            {"key": "x", "prior": 10.0, "scale": 2.0},
            {"key": "nested.y", "prior": 5.0, "scale": 5.0},
        ]
        params = {"x": 12.0, "nested": {"y": 0.0}}
        self.assertEqual(parameter_regularization_loss(params, specs), 2.0)

    def test_comsol_worker_draft_contains_expected_api_shape(self):
        worker = ROOT / "java" / "ComsolStepWorker.java"
        text = worker.read_text(encoding="utf-8")
        self.assertIn("ModelUtil.load", text)
        self.assertIn("model.param().loadFile", text)
        self.assertIn(".study(", text)
        self.assertIn(".run()", text)
        self.assertIn("gev1", text)
        self.assertIn("gmevescp2", text)
        self.assertIn("run_wafer", text)
        self.assertIn("parseNodes", text)
        self.assertIn("parameter_txt_order", text)
        self.assertIn("runDagNode", text)
        self.assertIn("inheritRve", text)
        self.assertIn("injectRveInputs", text)
        self.assertIn("resultFile", text)
        self.assertIn("manifest.json", text)
        self.assertIn("\"rve\"", text)
        self.assertIn("materials_state", text)
        self.assertIn("historyJson", text)
        self.assertIn("waferInputsToJson", text)
        self.assertIn("applyMaterialTarget", text)
        self.assertIn("applyStressTarget", text)
        self.assertIn("propertyGroup(elasticGroupTag)", text)
        self.assertIn("elasticPropertyName", text)
        self.assertIn("D_upper21", text)
        self.assertIn("symmetric_upper21", text)
        self.assertIn("parseInputTransfers", text)
        self.assertIn("MaterialTarget", text)
        self.assertIn("StressTarget", text)
        self.assertIn("target.material", text)
        self.assertIn("target.stress", text)
        self.assertNotIn("input_%s_d11", text)
        self.assertNotIn("input_%s_d22", text)

    def test_flow_responsibilities_are_split_into_focused_modules(self):
        import comsol_opt.flow_runner as flow_runner
        import comsol_opt.step_input as step_input
        import comsol_opt.summary as summary

        self.assertTrue(callable(flow_runner.run_flow))
        self.assertTrue(callable(step_input.build_step_input))
        self.assertTrue(callable(summary.write_summary))

    def test_config_io_exposes_generic_dump_data_for_yaml_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            yaml_path = root / "data.yaml"
            json_path = root / "data.json"
            dump_data(yaml_path, {"b": 2, "a": 1})
            dump_data(json_path, {"b": 2, "a": 1})
            self.assertEqual(load_config(yaml_path), {"a": 1, "b": 2})
            self.assertEqual(load_json(json_path), {"a": 1, "b": 2})

    def test_schema_validation_rejects_incomplete_rve_and_step_input(self):
        valid_rve = RveResult.from_dict(
            {
                "valid": True,
                "active": True,
                "source_step": "S01",
                "source_model": "model",
                "rho": 1.0,
                "stress_eff": {"sxx": 1.0, "syy": 2.0},
                "D": [[0.0] * 6 for _ in range(6)],
                "meta": {},
            }
        )
        self.assertEqual(valid_rve.source_step, "S01")
        valid_state = WaferState.from_dict(
            {
                "step_id": "S01",
                "step_name": "step",
                "wafer_result": {"bow_x_um": 0.0, "bow_y_um": 0.0, "kx": 0.0, "ky": 0.0},
                "device_rves": {"device_default": valid_rve_dict()},
                "wafer_inputs": {"mat1": valid_rve_dict()},
                "materials_state": {},
                "geometry_state": {},
                "history": [],
            }
        )
        self.assertEqual(valid_state.step_id, "S01")
        with self.assertRaises(ValueError):
            RveResult.from_dict({"D": [[0.0]]})
        with self.assertRaises(ValueError):
            StepInput.from_dict({"step_id": "S00"})


def valid_rve_dict():
    return {
        "valid": True,
        "active": True,
        "source_step": "S01",
        "source_model": "model",
        "rho": 1.0,
        "stress_eff": {"sxx": 1.0, "syy": 2.0},
        "D": [[0.0] * 6 for _ in range(6)],
        "meta": {},
    }


def minimal_v2_flow_and_step():
    target = {
        "material": {
            "component": "comp8",
            "tag": "mat_pillar_eff",
            "density_group": "def",
            "density_property": "density",
            "elastic_group": "anisotropic_eff",
            "elastic_property": "D",
            "elasticity_order": "standard",
            "D_format": "symmetric_upper21",
        },
        "stress": {
            "type": "initial_stress",
            "component": "comp8",
            "physics": "solid8",
            "parent_feature": "lemm1",
            "feature": "initstress_pillar",
            "property": "S0",
            "stress_format": "diag_sxx_syy",
        },
    }
    templates = {
        "templates": {
            "sc_model": {
                "path": "models/sc.mph",
                "node_type": "sc",
                "studies": {"stress": "std1", "cp": "cp1"},
                "extractors": {"rho": "gev_rho", "stress": "gev_stress", "D": "gev_D"},
            },
            "fecap_model_all": {
                "path": "models/fecap.mph",
                "node_type": "fecap",
                "studies": {"stress": "std4", "cp": "cp4"},
                "extractors": {"rho": "gev_rho", "stress": "gev_stress", "D": "gev_D"},
                "input_targets": {"pillar": target, "sc": target},
            },
            "die_model": {
                "path": "models/die.mph",
                "node_type": "die",
                "studies": {"stress": "std2", "cp": "cp2"},
                "extractors": {"rho": "gev_rho", "stress": "gev_stress", "D": "gev_D"},
                "input_targets": {
                    "decap1": target,
                    "decap2": target,
                    "decap3": target,
                    "fecap": target,
                },
            },
            "wafer_warp_model": {
                "path": "models/wafer.mph",
                "node_type": "wafer",
                "studies": {"stress": "std_w"},
                "extractors": {"bow_x_um": "gev_bow_x", "bow_y_um": "gev_bow_y"},
                "input_targets": {"die": target, "onon": target},
            },
        }
    }
    step = {
        "id": "S04",
        "name": "sc_etch",
        "layout_version": 2,
        "process_type": "v2",
        "update_rule": "init",
        "experiment_step": "S04",
        "nodes": {
            "pillar": {"action": "inherit"},
            "sc": {"action": "run", "template": "sc_model"},
            "decap1": {"action": "inherit"},
            "decap2": {"action": "inherit"},
            "decap3": {"action": "inherit"},
            "fecap": {
                "action": "run",
                "template": "fecap_model_all",
                "inputs": {
                    "pillar": {"ref": "rve.pillar"},
                    "sc": {"ref": "rve.sc"},
                },
            },
            "die": {
                "action": "run",
                "template": "die_model",
                "inputs": {
                    "decap1": {"ref": "rve.decap1"},
                    "decap2": {"ref": "rve.decap2"},
                    "decap3": {"ref": "rve.decap3"},
                    "fecap": {"ref": "rve.fecap"},
                },
            },
            "wafer": {
                "action": "run",
                "template": "wafer_warp_model",
                "inputs": {
                    "die": {"ref": "rve.die"},
                    "onon": {"ref": "rve.onon"},
                },
            },
        },
        "templates": templates,
        "parameter_files": [],
        "parameter_txt_order": ["global"],
        "raw_parameters": {},
        "parameters": {},
        "run_wafer": True,
    }
    flow = {"version": 2, "layout": "v2", "templates": templates, "steps": [step]}
    return flow, step


def v2_s02_flow_and_step():
    flow, base = minimal_v2_flow_and_step()
    step = {
        **base,
        "id": "S02",
        "name": "onon_dep",
        "nodes": {
            "pillar": {"action": "run", "template": "sc_model"},
            "sc": {"action": "default_from", "source": "pillar"},
            "decap1": {"action": "default_from", "source": "pillar"},
            "decap2": {"action": "default_from", "source": "pillar"},
            "decap3": {"action": "default_from", "source": "pillar"},
            "fecap": {"action": "default_from", "source": "pillar"},
            "die": {"action": "default_from", "source": "pillar"},
            "onon": {"action": "alias", "source": "pillar"},
            "wafer": {
                "action": "run",
                "template": "wafer_warp_model",
                "inputs": {
                    "die": {"ref": "rve.die"},
                    "onon": {"ref": "rve.onon"},
                },
            },
        },
    }
    flow = {**flow, "steps": [step]}
    return flow, step


def v2_s16_flow_and_step():
    flow, base = minimal_v2_flow_and_step()
    templates = dict(flow["templates"])
    templates["templates"] = dict(templates["templates"])
    templates["templates"]["fecap_stress_only"] = {
        **templates["templates"]["fecap_model_all"],
        "studies": {"stress": "std4", "cp": None},
    }
    step = {
        **base,
        "id": "S16",
        "name": "feslit_etch",
        "templates": templates,
        "nodes": {
            "pillar": {"action": "inherit"},
            "sc": {"action": "inherit"},
            "decap1": {"action": "inherit"},
            "decap2": {"action": "inherit"},
            "decap3": {"action": "inherit"},
            "fecap": {
                "action": "run",
                "template": "fecap_stress_only",
                "merge": {"D": "inherit_previous"},
                "inputs": {
                    "pillar": {"ref": "rve.pillar"},
                    "sc": {"ref": "rve.sc"},
                },
            },
            "die": {
                "action": "run",
                "template": "die_model",
                "inputs": {
                    "decap1": {"ref": "rve.decap1"},
                    "decap2": {"ref": "rve.decap2"},
                    "decap3": {"ref": "rve.decap3"},
                    "fecap": {"ref": "rve.fecap"},
                },
            },
            "wafer": {
                "action": "run",
                "template": "wafer_warp_model",
                "inputs": {
                    "die": {"ref": "rve.die"},
                    "onon": {"ref": "rve.onon"},
                },
            },
        },
    }
    flow = {"version": 2, "layout": "v2", "templates": templates, "steps": [step]}
    return flow, step


if __name__ == "__main__":
    unittest.main()
