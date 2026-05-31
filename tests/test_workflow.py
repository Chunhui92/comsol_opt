import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "python"
sys.path.insert(0, str(PYTHON_DIR))

from comsol_opt.config_io import load_config
from comsol_opt.flow import prepare_parameter_txt_set
from comsol_opt.loss import compute_loss_from_rows
from comsol_opt.parameter_txt import ParameterTxtSet


class ParameterTxtSetTests(unittest.TestCase):
    def test_updates_struct_stress_and_temp_parameter_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            struct = root / "struct.txt"
            stress = root / "stress.txt"
            temp = root / "temp.txt"
            struct.write_text("pillar_diameter = 120e-9\n", encoding="utf-8")
            stress.write_text("sigma_feol_x = 180e6\nsigma_w_fill 500e6\n", encoding="utf-8")
            temp.write_text("process_temp = 25\n", encoding="utf-8")

            txt_set = ParameterTxtSet(
                files={"struct": struct, "stress": stress, "temp": temp},
                mappings={
                    "geometry.pillar_diameter": {"file": "struct", "name": "pillar_diameter"},
                    "FEOL.sigma_init_x": {"file": "stress", "name": "sigma_feol_x"},
                    "W.sigma_fill": {"file": "stress", "name": "sigma_w_fill"},
                    "process.temperature_C": {"file": "temp", "name": "process_temp"},
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
                },
            )

            self.assertEqual(set(paths), {"struct", "stress", "temp"})
            self.assertIn("pillar_diameter = 1.25e-07", paths["struct"].read_text(encoding="utf-8"))
            stress_text = paths["stress"].read_text(encoding="utf-8")
            self.assertIn("sigma_feol_x = 175000000.0", stress_text)
            self.assertIn("sigma_w_fill 600000000.0", stress_text)
            self.assertIn("process_temp = 300", paths["temp"].read_text(encoding="utf-8"))


class WorkflowIntegrationTests(unittest.TestCase):
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
            self.assertIn("Completed 15 steps", completed.stdout)

            run_dir = run_root / "mock_test"
            s03 = json.loads((run_dir / "S03" / "state_out.json").read_text(encoding="utf-8"))
            self.assertEqual(s03["wafer_inputs"]["mat1"]["source_step"], "S02")
            self.assertEqual(s03["wafer_inputs"]["mat2"]["source_step"], "S03")

            s05a = json.loads((run_dir / "S05a" / "state_out.json").read_text(encoding="utf-8"))
            self.assertEqual(s05a["device_rves"]["device_default"]["source_step"], "S04")
            self.assertEqual(s05a["wafer_inputs"]["mat2"]["source_step"], "S05a")

            s07_input = json.loads((run_dir / "S07" / "step_input.json").read_text(encoding="utf-8"))
            s07_state = json.loads((run_dir / "S07" / "state_out.json").read_text(encoding="utf-8"))
            self.assertTrue(s07_input["templates"]["wafer"].endswith("wafer_with_asi_template.mph"))
            self.assertTrue(s07_state["wafer_inputs"]["aSi_layer"]["active"])

            s08_input = json.loads((run_dir / "S08" / "step_input.json").read_text(encoding="utf-8"))
            s08_state = json.loads((run_dir / "S08" / "state_out.json").read_text(encoding="utf-8"))
            self.assertTrue(s08_input["templates"]["wafer"].endswith("wafer_base_template.mph"))
            self.assertFalse(s08_state["wafer_inputs"]["aSi_layer"]["active"])

            mat3 = s08_state["wafer_inputs"]["mat3"]
            device2 = s08_state["device_rves"]["device2_for_mat3"]
            self.assertEqual(device2["source_step"], "S08")
            self.assertEqual(mat3["source_step"], "S08")
            self.assertNotEqual(
                s08_state["device_rves"]["device_default"]["stress_eff"]["sxx"],
                device2["stress_eff"]["sxx"],
            )

            with (run_dir / "summary.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 15)
            loss = compute_loss_from_rows(rows, scale_x=30.0, scale_y=30.0)
            self.assertGreater(loss["loss"], 0.0)

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
            self.assertTrue((run_dir / "S00" / "step_input.json").exists())
            self.assertFalse((run_dir / "S00" / "state_out.json").exists())

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
                "G0_init,G1_ONON_base",
            ]
            completed = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=True)
            self.assertIn("completed_stages=2", completed.stdout)

            calib_dir = run_root / "staged_test"
            g0 = calib_dir / "out" / "S00_G0_init"
            g1 = calib_dir / "out" / "S01_G1_ONON_base"
            self.assertTrue((g0 / "stage.log").exists())
            self.assertTrue((g0 / "best_params.yaml").exists())
            self.assertTrue((g0 / "best_summary.csv").exists())
            self.assertTrue((g1 / "stage.log").exists())
            self.assertTrue((g1 / "best_params.yaml").exists())
            self.assertTrue((calib_dir / "out" / "final_params.yaml").exists())

            g0_params = json.loads((g0 / "best_params.yaml").read_text(encoding="utf-8"))
            g1_trial_params = json.loads(
                (g1 / "trials" / "trial_0000" / "params_trial.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(g1_trial_params["FEOL"], g0_params["FEOL"])

            g1_log = (g1 / "stage.log").read_text(encoding="utf-8")
            self.assertIn("stage=G1_ONON_base", g1_log)
            self.assertIn("params=ONON.sigma_O_base,ONON.sigma_N_base", g1_log)


class ConfigTests(unittest.TestCase):
    def test_default_flow_config_loads_without_pyyaml(self):
        flow = load_config(ROOT / "configs" / "flow.yaml")
        self.assertEqual(flow["steps"][0]["id"], "S00")
        self.assertEqual(flow["steps"][-1]["id"], "S10")

    def test_default_paths_are_flattened(self):
        flow = load_config(ROOT / "configs" / "flow.yaml")
        template_paths = []
        for section in flow["templates"].values():
            template_paths.extend(section.values())
        self.assertTrue(template_paths)
        self.assertTrue(all(path.startswith("models/") for path in template_paths))
        self.assertTrue(all("models/templates/" not in path for path in template_paths))

        txt_set = prepare_parameter_txt_set(ROOT)
        self.assertEqual(txt_set.files["struct"], ROOT / "params" / "struct.txt")
        self.assertEqual(txt_set.files["stress"], ROOT / "params" / "stress.txt")
        self.assertEqual(txt_set.files["temp"], ROOT / "params" / "temp.txt")

    def test_comsol_worker_draft_contains_expected_api_shape(self):
        worker = ROOT / "java" / "ComsolStepWorker.java"
        text = worker.read_text(encoding="utf-8")
        self.assertIn("ModelUtil.load", text)
        self.assertIn("model.param().loadFile", text)
        self.assertIn(".study(", text)
        self.assertIn(".run()", text)
        self.assertIn("gev1", text)
        self.assertIn("gmevescp2", text)


if __name__ == "__main__":
    unittest.main()
