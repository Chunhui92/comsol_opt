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

        String stateInText = Files.readString(input.stateIn, StandardCharsets.UTF_8);
        Map<String, RveResult> rves = parseStateRves(stateInText);
        Map<String, ManifestEntry> manifest = new LinkedHashMap<>();
        WaferResult wafer = WaferResult.fromStateOrSkipped(stateInText);
        boolean waferSkipped = true;

        for (NodeRun node : input.nodes) {
            NodeResult result = runDagNode(input, node, rves);
            if (result.rve != null) {
                rves.put(node.id, result.rve);
                writeNodeResult(input.outputDir, node, result.rve.toJson());
            }
            if (result.wafer != null) {
                wafer = result.wafer;
                waferSkipped = false;
                writeNodeResult(input.outputDir, node, result.wafer.toJson());
            }
            manifest.put(node.id, result.manifest);
        }

        writeWorkerOutputs(input, rves, wafer, waferSkipped, manifest);
    }

    private static NodeResult runDagNode(StepInput input, NodeRun node, Map<String, RveResult> rves) throws Exception {
        if ("inherit".equals(node.action)) {
            RveResult inherited = inheritRve(input, node, rves);
            return NodeResult.rve(inherited, ManifestEntry.inherited(node, inherited.sourceStep));
        }
        if (!"run".equals(node.action)) {
            throw new IllegalArgumentException(input.stepId + " node " + node.id + " has unknown action " + node.action);
        }
        if ("wafer".equals(node.type)) {
            if (!input.runWafer) {
                return NodeResult.skipped(ManifestEntry.skipped(node));
            }
            WaferResult wafer = runWaferModel(node, input.parameterFiles, rves);
            return NodeResult.wafer(wafer, ManifestEntry.ran(node, input.stepId));
        }

        RveResult rve = runRveModel(node, input.parameterFiles, rves);
        rve.sourceStep = input.stepId;
        rve.sourceModel = node.templateKey;
        return NodeResult.rve(rve, ManifestEntry.ran(node, input.stepId));
    }

    private static RveResult inheritRve(StepInput input, NodeRun node, Map<String, RveResult> rves) {
        RveResult inherited = rves.get(node.id);
        if (inherited == null || !inherited.valid) {
            throw new IllegalStateException(input.stepId + " cannot inherit invalid RVE " + node.id);
        }
        return inherited;
    }

    private static RveResult runRveModel(NodeRun node, List<Path> parameterFiles, Map<String, RveResult> rves) throws Exception {
        Model model = ModelUtil.load("model_" + sanitize(node.id), node.templatePath);
        loadParameterFiles(model, parameterFiles);
        injectRveInputs(model, node.inputs, rves);
        model.study(node.extractorTags.getOrDefault("study", DEFAULT_STUDY_TAG)).run();

        double rho = firstReal(model, node.extractorTags.getOrDefault("rho", DEFAULT_RHO_GEV_TAG), 0.0);
        double[] stress = realVector(model, node.extractorTags.getOrDefault("stress", DEFAULT_STRESS_GEV_TAG), 2);
        String matrixTag = node.extractorTags.getOrDefault("matrix_feature", DEFAULT_D_MATRIX_TAG);
        double[][] stiffness = matrix6x6(model, matrixTag);

        ModelUtil.remove(model.tag());
        return new RveResult(true, true, rho, stress[0], stress[1], stiffness);
    }

    private static WaferResult runWaferModel(NodeRun node, List<Path> parameterFiles, Map<String, RveResult> rves) throws Exception {
        Model model = ModelUtil.load("model_wafer", node.templatePath);
        loadParameterFiles(model, parameterFiles);
        injectRveInputs(model, node.inputs, rves);
        model.study(node.extractorTags.getOrDefault("study", DEFAULT_STUDY_TAG)).run();

        double bowX = firstReal(model, node.extractorTags.getOrDefault("bow_x", DEFAULT_BOW_GEV_TAG), 0.0);
        double bowY = firstReal(model, node.extractorTags.getOrDefault("bow_y", DEFAULT_BOW_GEV_TAG), 0.0);
        double kx = firstReal(model, node.extractorTags.getOrDefault("kx", DEFAULT_BOW_GEV_TAG), 0.0);
        double ky = firstReal(model, node.extractorTags.getOrDefault("ky", DEFAULT_BOW_GEV_TAG), 0.0);
        ModelUtil.remove(model.tag());
        return new WaferResult(bowX, bowY, kx, ky);
    }

    private static void loadParameterFiles(Model model, List<Path> parameterFiles) {
        for (Path path : parameterFiles) {
            model.param().loadFile(path.toString());
        }
    }

    private static void injectRveInputs(Model model, Map<String, String> inputs, Map<String, RveResult> rves) {
        for (Map.Entry<String, String> input : inputs.entrySet()) {
            if (!input.getValue().startsWith("rve.")) {
                continue;
            }
            String sourceId = input.getValue().substring("rve.".length());
            RveResult rve = rves.get(sourceId);
            if (rve == null || !rve.valid) {
                throw new IllegalStateException("Missing valid upstream RVE " + input.getValue());
            }
            String slot = sanitize(input.getKey());
            model.param().set(String.format("input_%s_sxx", slot), Double.toString(rve.sxx));
            model.param().set(String.format("input_%s_syy", slot), Double.toString(rve.syy));
            model.param().set(String.format("input_%s_rho", slot), Double.toString(rve.rho));
            model.param().set(String.format("input_%s_d11", slot), Double.toString(rve.d[0][0]));
            model.param().set(String.format("input_%s_d22", slot), Double.toString(rve.d[1][1]));
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

    private static void writeNodeResult(Path outputDir, NodeRun node, String payload) throws IOException {
        if (node.resultFile == null || node.resultFile.isEmpty()) {
            return;
        }
        Files.writeString(outputDir.resolve(node.resultFile), payload + "\n", StandardCharsets.UTF_8);
    }

    private static void writeWorkerOutputs(
        StepInput input,
        Map<String, RveResult> rves,
        WaferResult wafer,
        boolean waferSkipped,
        Map<String, ManifestEntry> manifest
    ) throws IOException {
        String stepResult = "{\n"
            + "  \"status\": \"success\",\n"
            + "  \"step_id\": " + json(input.stepId) + ",\n"
            + "  \"wafer_result\": " + wafer.toJson() + ",\n"
            + "  \"wafer_skipped\": " + waferSkipped + "\n"
            + "}\n";
        Files.writeString(input.outputDir.resolve("step_result.json"), stepResult, StandardCharsets.UTF_8);
        Files.writeString(input.outputDir.resolve("manifest.json"), manifestToJson(input, manifest), StandardCharsets.UTF_8);

        StringBuilder state = new StringBuilder();
        state.append("{\n");
        state.append("  \"step_id\": ").append(json(input.stepId)).append(",\n");
        state.append("  \"step_name\": ").append(json(input.stepName)).append(",\n");
        state.append("  \"rve\": ").append(rveMapToJson(rves)).append(",\n");
        state.append("  \"wafer_result\": ").append(wafer.toJson()).append(",\n");
        state.append("  \"materials_state\": {},\n");
        state.append("  \"geometry_state\": {},\n");
        state.append("  \"history\": []\n");
        state.append("}\n");
        Files.writeString(input.outputDir.resolve("state_out.json"), state.toString(), StandardCharsets.UTF_8);
    }

    private static String manifestToJson(StepInput input, Map<String, ManifestEntry> manifest) {
        StringBuilder builder = new StringBuilder();
        builder.append("{\n  \"step_id\": ").append(json(input.stepId)).append(",\n  \"nodes\": {");
        int index = 0;
        for (Map.Entry<String, ManifestEntry> entry : manifest.entrySet()) {
            if (index++ > 0) {
                builder.append(",");
            }
            builder.append("\n    ").append(json(entry.getKey())).append(": ").append(entry.getValue().toJson());
        }
        if (!manifest.isEmpty()) {
            builder.append("\n  ");
        }
        builder.append("}\n}\n");
        return builder.toString();
    }

    private static Map<String, RveResult> parseStateRves(String text) {
        Map<String, RveResult> values = new LinkedHashMap<>();
        String rveText = objectText(text, "rve");
        for (String object : splitTopLevelObjects(rveText)) {
            String key = objectKey(object);
            if (!key.isEmpty()) {
                values.put(key, RveResult.fromJson(object));
            }
        }
        return values;
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
        final boolean runWafer;
        final Path stateIn;
        final Path outputDir;
        final List<Path> parameterFiles;
        final List<NodeRun> nodes;

        private StepInput(
            String stepId,
            String stepName,
            boolean runWafer,
            Path stateIn,
            Path outputDir,
            List<Path> parameterFiles,
            List<NodeRun> nodes
        ) {
            this.stepId = stepId;
            this.stepName = stepName;
            this.runWafer = runWafer;
            this.stateIn = stateIn;
            this.outputDir = outputDir;
            this.parameterFiles = parameterFiles;
            this.nodes = nodes;
        }

        static StepInput load(Path path) throws IOException {
            String text = Files.readString(path, StandardCharsets.UTF_8);
            return new StepInput(
                stringValue(text, "step_id"),
                stringValue(text, "step_name"),
                booleanValue(text, "run_wafer"),
                Path.of(stringValue(text, "state_in")),
                Path.of(stringValue(text, "output_dir")),
                parameterFilesInOrder(text),
                parseNodes(text)
            );
        }
    }

    private static final class NodeRun {
        final String id;
        final String type;
        final String action;
        final String templateKey;
        final String templatePath;
        final String output;
        final String resultFile;
        final Map<String, String> inputs;
        final Map<String, String> extractorTags;

        private NodeRun(
            String id,
            String type,
            String action,
            String templateKey,
            String templatePath,
            String output,
            String resultFile,
            Map<String, String> inputs,
            Map<String, String> extractorTags
        ) {
            this.id = id;
            this.type = type;
            this.action = action;
            this.templateKey = templateKey;
            this.templatePath = templatePath;
            this.output = output;
            this.resultFile = resultFile;
            this.inputs = inputs;
            this.extractorTags = extractorTags;
        }
    }

    private static List<NodeRun> parseNodes(String text) {
        List<NodeRun> nodes = new ArrayList<>();
        String array = arrayText(text, "nodes");
        for (String nodeText : splitTopLevelArrayObjects(array)) {
            nodes.add(
                new NodeRun(
                    stringValue(nodeText, "id"),
                    stringValue(nodeText, "type"),
                    stringValue(nodeText, "action"),
                    optionalStringValue(nodeText, "template"),
                    optionalStringValue(nodeText, "template_path"),
                    optionalStringValue(nodeText, "output"),
                    optionalStringValue(nodeText, "result_file"),
                    objectStrings(objectText(nodeText, "inputs")),
                    objectStrings(objectText(nodeText, "extractor_tags"))
                )
            );
        }
        return nodes;
    }

    private static List<Path> parameterFilesInOrder(String text) {
        Map<String, String> paths = objectStrings(objectText(text, "parameter_txt_paths"));
        List<Path> files = new ArrayList<>();
        for (String key : stringArray(text, "parameter_txt_order")) {
            if (!paths.containsKey(key)) {
                throw new IllegalArgumentException("Missing parameter_txt_paths entry for " + key);
            }
            files.add(Path.of(paths.get(key)));
        }
        if (files.isEmpty()) {
            for (String value : paths.values()) {
                files.add(Path.of(value));
            }
        }
        return files;
    }

    private static final class NodeResult {
        final RveResult rve;
        final WaferResult wafer;
        final ManifestEntry manifest;

        private NodeResult(RveResult rve, WaferResult wafer, ManifestEntry manifest) {
            this.rve = rve;
            this.wafer = wafer;
            this.manifest = manifest;
        }

        static NodeResult rve(RveResult rve, ManifestEntry manifest) {
            return new NodeResult(rve, null, manifest);
        }

        static NodeResult wafer(WaferResult wafer, ManifestEntry manifest) {
            return new NodeResult(null, wafer, manifest);
        }

        static NodeResult skipped(ManifestEntry manifest) {
            return new NodeResult(null, null, manifest);
        }
    }

    private static final class ManifestEntry {
        final String action;
        final String status;
        final String template;
        final String sourceStep;
        final String output;

        private ManifestEntry(String action, String status, String template, String sourceStep, String output) {
            this.action = action;
            this.status = status;
            this.template = template;
            this.sourceStep = sourceStep;
            this.output = output;
        }

        static ManifestEntry ran(NodeRun node, String sourceStep) {
            return new ManifestEntry(node.action, "success", node.templateKey, sourceStep, node.output);
        }

        static ManifestEntry inherited(NodeRun node, String sourceStep) {
            return new ManifestEntry(node.action, "success", node.templateKey, sourceStep, node.output);
        }

        static ManifestEntry skipped(NodeRun node) {
            return new ManifestEntry(node.action, "skipped", node.templateKey, "", node.output);
        }

        String toJson() {
            return "{"
                + "\"action\":" + json(action)
                + ",\"status\":" + json(status)
                + ",\"template\":" + json(template == null ? "" : template)
                + ",\"source_step\":" + json(sourceStep)
                + ",\"output\":" + json(output == null ? "" : output)
                + "}";
        }
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

        static RveResult fromJson(String text) {
            RveResult rve = new RveResult(
                booleanValue(text, "valid"),
                booleanValue(text, "active"),
                doubleValue(text, "rho", 0.0),
                doubleValue(objectText(text, "stress_eff"), "sxx", 0.0),
                doubleValue(objectText(text, "stress_eff"), "syy", 0.0),
                matrix6x6FromJson(arrayText(text, "D"))
            );
            rve.sourceStep = optionalStringValue(text, "source_step");
            rve.sourceModel = optionalStringValue(text, "source_model");
            return rve;
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

        static WaferResult fromStateOrSkipped(String stateText) {
            String wafer = objectText(stateText, "wafer_result");
            if (wafer.isEmpty()) {
                return skipped();
            }
            return new WaferResult(
                doubleValue(wafer, "bow_x_um", 0.0),
                doubleValue(wafer, "bow_y_um", 0.0),
                doubleValue(wafer, "kx", 0.0),
                doubleValue(wafer, "ky", 0.0)
            );
        }

        String toJson() {
            return "{\"bow_x_um\":" + bowX + ",\"bow_y_um\":" + bowY + ",\"kx\":" + kx + ",\"ky\":" + ky + "}";
        }

        static WaferResult skipped() {
            return new WaferResult(0.0, 0.0, 0.0, 0.0);
        }
    }

    private static String objectKey(String text) {
        Matcher matcher = Pattern.compile("^\\s*\"([^\"]+)\"\\s*:").matcher(text);
        return matcher.find() ? matcher.group(1) : "";
    }

    private static String objectText(String text, String key) {
        int keyIndex = text.indexOf("\"" + key + "\"");
        if (keyIndex < 0) {
            return "";
        }
        int start = text.indexOf("{", keyIndex);
        if (start < 0) {
            return "";
        }
        int end = matchingIndex(text, start, '{', '}');
        return end < 0 ? "" : text.substring(start + 1, end);
    }

    private static String arrayText(String text, String key) {
        int keyIndex = text.indexOf("\"" + key + "\"");
        if (keyIndex < 0) {
            return "";
        }
        int start = text.indexOf("[", keyIndex);
        if (start < 0) {
            return "";
        }
        int end = matchingIndex(text, start, '[', ']');
        return end < 0 ? "" : text.substring(start + 1, end);
    }

    private static int matchingIndex(String text, int start, char open, char close) {
        int depth = 0;
        boolean inString = false;
        boolean escaped = false;
        for (int i = start; i < text.length(); i++) {
            char ch = text.charAt(i);
            if (escaped) {
                escaped = false;
                continue;
            }
            if (ch == '\\') {
                escaped = true;
                continue;
            }
            if (ch == '"') {
                inString = !inString;
                continue;
            }
            if (inString) {
                continue;
            }
            if (ch == open) {
                depth++;
            } else if (ch == close) {
                depth--;
                if (depth == 0) {
                    return i;
                }
            }
        }
        return -1;
    }

    private static List<String> splitTopLevelArrayObjects(String text) {
        List<String> values = new ArrayList<>();
        int index = 0;
        while (index < text.length()) {
            int start = text.indexOf("{", index);
            if (start < 0) {
                break;
            }
            int end = matchingIndex(text, start, '{', '}');
            if (end < 0) {
                break;
            }
            values.add(text.substring(start, end + 1));
            index = end + 1;
        }
        return values;
    }

    private static List<String> splitTopLevelObjects(String text) {
        List<String> values = new ArrayList<>();
        Matcher matcher = Pattern.compile("\"([^\"]+)\"\\s*:").matcher(text);
        while (matcher.find()) {
            int start = text.indexOf("{", matcher.end());
            if (start < 0) {
                continue;
            }
            int end = matchingIndex(text, start, '{', '}');
            if (end < 0) {
                continue;
            }
            values.add(text.substring(matcher.start(), end + 1));
            matcher.region(end + 1, text.length());
        }
        return values;
    }

    private static Map<String, String> objectStrings(String text) {
        Map<String, String> values = new LinkedHashMap<>();
        Matcher matcher = Pattern.compile("\"([^\"]+)\"\\s*:\\s*\"([^\"]*)\"").matcher(text);
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
        String value = optionalStringValue(text, key);
        if (value.isEmpty()) {
            throw new IllegalArgumentException("Missing JSON string key: " + key);
        }
        return value;
    }

    private static String optionalStringValue(String text, String key) {
        Matcher matcher = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\"([^\"]*)\"").matcher(text);
        return matcher.find() ? matcher.group(1) : "";
    }

    private static boolean booleanValue(String text, String key) {
        Matcher matcher = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*(true|false)").matcher(text);
        if (!matcher.find()) {
            throw new IllegalArgumentException("Missing JSON boolean key: " + key);
        }
        return Boolean.parseBoolean(matcher.group(1));
    }

    private static double doubleValue(String text, String key, double fallback) {
        Matcher matcher = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*([-+0-9.eE]+)").matcher(text);
        return matcher.find() ? Double.parseDouble(matcher.group(1)) : fallback;
    }

    private static double[][] matrix6x6FromJson(String text) {
        Matcher matcher = Pattern.compile("[-+0-9.eE]+").matcher(text);
        double[][] matrix = new double[6][6];
        int index = 0;
        while (matcher.find() && index < 36) {
            matrix[index / 6][index % 6] = Double.parseDouble(matcher.group());
            index++;
        }
        return matrix;
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
