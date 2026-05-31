import com.comsol.model.Model;
import com.comsol.model.util.ModelUtil;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class ComsolStepWorker {
    private static final String DEFAULT_STUDY_TAG = "std1";
    private static final String DEFAULT_STRESS_GEV_TAG = "gev1";
    private static final String DEFAULT_RHO_GEV_TAG = "gev_rho";
    private static final String DEFAULT_D_MATRIX_TAG = "gmevescp2";
    private static final String DEFAULT_BOW_GEV_TAG = "gev_bow";

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("Usage: ComsolStepWorker <step_input.json>");
        }
        StepInput input = StepInput.load(Path.of(args[0]));
        Files.createDirectories(input.outputDir);

        Map<String, RveResult> deviceResults = new LinkedHashMap<>();
        for (ModelRun device : input.devices) {
            RveResult rve = runRveModel(device.name, device.templatePath, input.parameterFiles);
            rve.sourceStep = input.stepId;
            rve.sourceModel = device.templateKey;
            deviceResults.put(device.name, rve);
        }

        Map<String, RveResult> matResults = new LinkedHashMap<>();
        for (String matName : input.runMats) {
            String templatePath = input.matTemplates.get(matName);
            RveResult rve = runRveModel(matName, templatePath, input.parameterFiles);
            rve.sourceStep = input.stepId;
            rve.sourceModel = matName + "_template";
            matResults.put(matName, rve);
        }

        WaferResult wafer = runWaferModel(input.templatesWafer, input.parameterFiles);
        writeWorkerOutputs(input, deviceResults, matResults, wafer);
    }

    private static RveResult runRveModel(String tag, String mphPath, List<Path> parameterFiles) throws Exception {
        Model model = ModelUtil.load("model_" + sanitize(tag), mphPath);
        loadParameterFiles(model, parameterFiles);
        model.study(DEFAULT_STUDY_TAG).run();

        double rho = firstReal(model, DEFAULT_RHO_GEV_TAG, 0.0);
        double[] stress = realVector(model, DEFAULT_STRESS_GEV_TAG, 2);
        double[][] stiffness = matrix6x6(model, DEFAULT_D_MATRIX_TAG);

        ModelUtil.remove(model.tag());
        return new RveResult(true, true, rho, stress[0], stress[1], stiffness);
    }

    private static WaferResult runWaferModel(String mphPath, List<Path> parameterFiles) throws Exception {
        Model model = ModelUtil.load("model_wafer", mphPath);
        loadParameterFiles(model, parameterFiles);
        model.study(DEFAULT_STUDY_TAG).run();

        double[] bow = realVector(model, DEFAULT_BOW_GEV_TAG, 4);
        ModelUtil.remove(model.tag());
        return new WaferResult(bow[0], bow[1], bow[2], bow[3]);
    }

    private static void loadParameterFiles(Model model, List<Path> parameterFiles) {
        for (Path path : parameterFiles) {
            model.param().loadFile(path.toString());
        }
    }

    private static double firstReal(Model model, String numericalTag, double fallback) {
        double[] values = realVector(model, numericalTag, 1);
        return values.length == 0 ? fallback : values[0];
    }

    private static double[] realVector(Model model, String numericalTag, int minLength) {
        double[][] raw = model.result().numerical(numericalTag).getReal();
        List<Double> values = new ArrayList<>();
        for (double[] row : raw) {
            for (double value : row) {
                values.add(value);
            }
        }
        while (values.size() < minLength) {
            values.add(0.0);
        }
        double[] result = new double[values.size()];
        for (int i = 0; i < values.size(); i++) {
            result[i] = values.get(i);
        }
        return result;
    }

    private static double[][] matrix6x6(Model model, String numericalTag) {
        double[] flat = realVector(model, numericalTag, 36);
        double[][] matrix = new double[6][6];
        for (int i = 0; i < 36; i++) {
            matrix[i / 6][i % 6] = flat[i];
        }
        return matrix;
    }

    private static void writeWorkerOutputs(
        StepInput input,
        Map<String, RveResult> deviceResults,
        Map<String, RveResult> matResults,
        WaferResult wafer
    ) throws IOException {
        String stepResult = "{\n"
            + "  \"status\": \"success\",\n"
            + "  \"step_id\": " + json(input.stepId) + ",\n"
            + "  \"wafer_result\": " + wafer.toJson() + "\n"
            + "}\n";
        Files.writeString(input.outputDir.resolve("step_result.json"), stepResult, StandardCharsets.UTF_8);

        StringBuilder state = new StringBuilder();
        state.append("{\n");
        state.append("  \"step_id\": ").append(json(input.stepId)).append(",\n");
        state.append("  \"step_name\": ").append(json(input.stepName)).append(",\n");
        state.append("  \"wafer_result\": ").append(wafer.toJson()).append(",\n");
        state.append("  \"device_rves\": ").append(rveMapToJson(deviceResults)).append(",\n");
        state.append("  \"wafer_inputs\": ").append(rveMapToJson(matResults)).append("\n");
        state.append("}\n");
        Files.writeString(input.outputDir.resolve("state_out.json"), state.toString(), StandardCharsets.UTF_8);
    }

    private static String rveMapToJson(Map<String, RveResult> values) {
        StringBuilder json = new StringBuilder("{");
        int index = 0;
        for (Map.Entry<String, RveResult> entry : values.entrySet()) {
            if (index++ > 0) {
                json.append(",");
            }
            json.append("\n    ").append(json(entry.getKey())).append(": ").append(entry.getValue().toJson());
        }
        if (!values.isEmpty()) {
            json.append("\n  ");
        }
        json.append("}");
        return json.toString();
    }

    private static String sanitize(String value) {
        return value.replaceAll("[^A-Za-z0-9_]", "_");
    }

    private static String json(String value) {
        return "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }

    private static final class StepInput {
        final String stepId;
        final String stepName;
        final String templatesWafer;
        final Path outputDir;
        final List<Path> parameterFiles;
        final List<ModelRun> devices;
        final List<String> runMats;
        final Map<String, String> matTemplates;

        private StepInput(
            String stepId,
            String stepName,
            String templatesWafer,
            Path outputDir,
            List<Path> parameterFiles,
            List<ModelRun> devices,
            List<String> runMats,
            Map<String, String> matTemplates
        ) {
            this.stepId = stepId;
            this.stepName = stepName;
            this.templatesWafer = templatesWafer;
            this.outputDir = outputDir;
            this.parameterFiles = parameterFiles;
            this.devices = devices;
            this.runMats = runMats;
            this.matTemplates = matTemplates;
        }

        static StepInput load(Path path) throws IOException {
            String text = Files.readString(path, StandardCharsets.UTF_8);
            List<Path> parameterFiles = List.of(
                Path.of(stringValue(text, "struct")),
                Path.of(stringValue(text, "stress")),
                Path.of(stringValue(text, "temp"))
            );
            Map<String, String> matTemplates = objectStrings(objectText(text, "mats"));
            return new StepInput(
                stringValue(text, "step_id"),
                stringValue(text, "step_name"),
                stringValue(text, "wafer"),
                Path.of(stringValue(text, "output_dir")),
                parameterFiles,
                parseDevices(text),
                stringArray(text, "run_mats"),
                matTemplates
            );
        }
    }

    private static final class ModelRun {
        final String name;
        final String templateKey;
        final String templatePath;

        private ModelRun(String name, String templateKey, String templatePath) {
            this.name = name;
            this.templateKey = templateKey;
            this.templatePath = templatePath;
        }
    }

    private static List<ModelRun> parseDevices(String text) {
        Map<String, String> deviceTemplatePaths = objectStrings(objectText(text, "devices"));
        List<ModelRun> runs = new ArrayList<>();
        Matcher matcher = Pattern.compile("\\{\\s*\"name\"\\s*:\\s*\"([^\"]+)\"\\s*,\\s*\"template_key\"\\s*:\\s*\"([^\"]+)\"\\s*}").matcher(text);
        while (matcher.find()) {
            String name = matcher.group(1);
            String templateKey = matcher.group(2);
            runs.add(new ModelRun(name, templateKey, deviceTemplatePaths.get(name)));
        }
        return runs;
    }

    private static String objectText(String text, String key) {
        Pattern pattern = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\\{([^{}]*(?:\\{[^{}]*}\\s*,?[^{}]*)*)}", Pattern.DOTALL);
        Matcher matcher = pattern.matcher(text);
        if (!matcher.find()) {
            return "";
        }
        return matcher.group(1);
    }

    private static Map<String, String> objectStrings(String text) {
        Map<String, String> values = new LinkedHashMap<>();
        Matcher matcher = Pattern.compile("\"([^\"]+)\"\\s*:\\s*\"([^\"]+)\"").matcher(text);
        while (matcher.find()) {
            values.put(matcher.group(1), matcher.group(2));
        }
        return values;
    }

    private static List<String> stringArray(String text, String key) {
        Matcher matcher = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\\[([^]]*)]", Pattern.DOTALL).matcher(text);
        List<String> values = new ArrayList<>();
        if (matcher.find()) {
            Matcher item = Pattern.compile("\"([^\"]+)\"").matcher(matcher.group(1));
            while (item.find()) {
                values.add(item.group(1));
            }
        }
        return values;
    }

    private static String stringValue(String text, String key) {
        Matcher matcher = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\"([^\"]+)\"").matcher(text);
        if (!matcher.find()) {
            throw new IllegalArgumentException("Missing JSON string key: " + key);
        }
        return matcher.group(1);
    }

    private static final class RveResult {
        String sourceStep = "";
        String sourceModel = "";
        final boolean valid;
        final boolean active;
        final double rho;
        final double sxx;
        final double syy;
        final double[][] d;

        private RveResult(boolean valid, boolean active, double rho, double sxx, double syy, double[][] d) {
            this.valid = valid;
            this.active = active;
            this.rho = rho;
            this.sxx = sxx;
            this.syy = syy;
            this.d = d;
        }

        String toJson() {
            return "{"
                + "\"valid\":" + valid
                + ",\"active\":" + active
                + ",\"source_step\":" + json(sourceStep)
                + ",\"source_model\":" + json(sourceModel)
                + ",\"rho\":" + rho
                + ",\"stress_eff\":{\"sxx\":" + sxx + ",\"syy\":" + syy + "}"
                + ",\"D\":" + matrixToJson(d)
                + ",\"meta\":{}"
                + "}";
        }
    }

    private static final class WaferResult {
        final double bowX;
        final double bowY;
        final double kx;
        final double ky;

        private WaferResult(double bowX, double bowY, double kx, double ky) {
            this.bowX = bowX;
            this.bowY = bowY;
            this.kx = kx;
            this.ky = ky;
        }

        String toJson() {
            return "{\"bow_x_um\":" + bowX + ",\"bow_y_um\":" + bowY + ",\"kx\":" + kx + ",\"ky\":" + ky + "}";
        }
    }

    private static String matrixToJson(double[][] matrix) {
        StringBuilder builder = new StringBuilder("[");
        for (int row = 0; row < matrix.length; row++) {
            if (row > 0) {
                builder.append(",");
            }
            builder.append("[");
            for (int col = 0; col < matrix[row].length; col++) {
                if (col > 0) {
                    builder.append(",");
                }
                builder.append(matrix[row][col]);
            }
            builder.append("]");
        }
        builder.append("]");
        return builder.toString();
    }
}
