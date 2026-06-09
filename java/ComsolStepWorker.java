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
    private static final String[] D_KEYS = {
        "D11", "D12", "D13", "D14", "D15", "D16",
        "D22", "D23", "D24", "D25", "D26",
        "D33", "D34", "D35", "D36",
        "D44", "D45", "D46",
        "D55", "D56", "D66"
    };

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

        writeWorkerOutputs(input, stateInText, rves, wafer, waferSkipped, manifest);
    }

    private static NodeResult runDagNode(StepInput input, NodeRun node, Map<String, RveResult> rves) throws Exception {
        if ("inherit".equals(node.action)) {
            RveResult inherited = inheritRve(input, node.id, rves);
            return NodeResult.rve(inherited, ManifestEntry.inherited(node, inherited.sourceStep));
        }
        if ("default_from".equals(node.action) || "alias".equals(node.action)) {
            RveResult source = inheritRve(input, node.source, rves).copy();
            source.sourceNode = node.source;
            return NodeResult.rve(source, ManifestEntry.inherited(node, source.sourceStep));
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

        RveResult previous = rves.get(node.id);
        RveResult rve = runRveModel(node, input.parameterFiles, rves, previous);
        rve.sourceStep = input.stepId;
        rve.sourceModel = node.templateKey;
        rve.sourceNode = node.id;
        return NodeResult.rve(rve, ManifestEntry.ran(node, input.stepId));
    }

    private static RveResult inheritRve(StepInput input, String nodeId, Map<String, RveResult> rves) {
        RveResult inherited = rves.get(nodeId);
        if (inherited == null || !inherited.valid) {
            throw new IllegalStateException(input.stepId + " cannot inherit invalid RVE " + nodeId);
        }
        return inherited;
    }

    private static RveResult runRveModel(NodeRun node, List<Path> parameterFiles, Map<String, RveResult> rves, RveResult previous) throws Exception {
        Model model = ModelUtil.load("model_" + sanitize(node.id), node.templatePath);
        writeInputParameterTxts(node, rves);
        loadParameterFiles(model, parameterFiles);
        loadParameterFiles(model, node.inputParameterTxtPaths);
        model.study(node.studies.getOrDefault("stress", DEFAULT_STUDY_TAG)).run();
        String cpStudy = node.studies.getOrDefault("cp", "");
        if (!cpStudy.isEmpty()) {
            model.study(cpStudy).run();
        }
        ModelUtil.remove(model.tag());
        return parseOutputTxt(node.outputTxtPath, node.id, previous);
    }

    private static WaferResult runWaferModel(NodeRun node, List<Path> parameterFiles, Map<String, RveResult> rves) throws Exception {
        Model model = ModelUtil.load("model_wafer", node.templatePath);
        writeInputParameterTxts(node, rves);
        loadParameterFiles(model, parameterFiles);
        loadParameterFiles(model, node.inputParameterTxtPaths);
        model.study(node.studies.getOrDefault("stress", DEFAULT_STUDY_TAG)).run();
        ModelUtil.remove(model.tag());
        return parseWaferOutputTxt(node.outputTxtPath);
    }

    private static void loadParameterFiles(Model model, List<Path> parameterFiles) {
        for (Path path : parameterFiles) {
            model.param().loadFile(path.toString());
        }
    }

    private static void writeInputParameterTxts(NodeRun node, Map<String, RveResult> rves) throws IOException {
        for (Map.Entry<String, Path> entry : node.inputParameterTxtPathBySlot.entrySet()) {
            String slot = entry.getKey();
            String source = node.inputs.getOrDefault(slot, slot);
            RveResult rve = rves.get(source);
            if (rve == null || !rve.valid) {
                throw new IllegalStateException(node.id + " missing valid RVE input " + source);
            }
            writeRveParameterTxt(entry.getValue(), slot, rve);
        }
    }

    private static void writeRveParameterTxt(Path path, String slot, RveResult rve) throws IOException {
        Files.createDirectories(path.getParent());
        String prefix = "rve_" + slot;
        StringBuilder text = new StringBuilder();
        text.append(prefix).append("_rho\t").append(rve.rho).append("[kg/m^3]\teffective density\n");
        text.append(prefix).append("_sxx\t").append(rve.sxx).append("[Pa]\tresidual stress xx\n");
        text.append(prefix).append("_syy\t").append(rve.syy).append("[Pa]\tresidual stress yy\n");
        int index = 0;
        for (int row = 0; row < 6; row++) {
            for (int col = row; col < 6; col++) {
                text.append(prefix).append("_").append(D_KEYS[index++]).append("\t").append(rve.d[row][col]).append("[Pa]\tstiffness\n");
            }
        }
        Files.writeString(path, text.toString(), StandardCharsets.UTF_8);
    }

    private static RveResult parseOutputTxt(Path path, String nodeId, RveResult previous) throws IOException {
        Map<String, Double> values = parseParameterTxt(path);
        double[][] matrix = new double[6][6];
        boolean hasFullD = true;
        for (String key : D_KEYS) {
            hasFullD = hasFullD && values.containsKey(key);
        }
        if (hasFullD) {
            fillUpperTriangle(matrix, values);
        } else if (previous != null) {
            matrix = previous.d;
        } else {
            throw new IllegalArgumentException(nodeId + " output txt missing D fields");
        }
        return new RveResult(
            true,
            true,
            values.getOrDefault("rho", 0.0),
            values.getOrDefault("sxx", 0.0),
            values.getOrDefault("syy", 0.0),
            matrix
        );
    }

    private static WaferResult parseWaferOutputTxt(Path path) throws IOException {
        Map<String, Double> values = parseParameterTxt(path);
        return new WaferResult(
            values.getOrDefault("bow_x_um", 0.0),
            values.getOrDefault("bow_y_um", 0.0),
            values.getOrDefault("kx", 0.0),
            values.getOrDefault("ky", 0.0)
        );
    }

    private static Map<String, Double> parseParameterTxt(Path path) throws IOException {
        Map<String, Double> values = new LinkedHashMap<>();
        for (String line : Files.readAllLines(path, StandardCharsets.UTF_8)) {
            String trimmed = line.trim();
            if (trimmed.isEmpty() || trimmed.startsWith("#") || trimmed.startsWith("%")) {
                continue;
            }
            String[] parts = trimmed.split("\\s+");
            if (parts.length >= 2) {
                values.put(parts[0], expressionToDouble(parts[1]));
            }
        }
        return values;
    }

    private static double expressionToDouble(String expression) {
        Matcher matcher = Pattern.compile("^([-+]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][-+]?\\d+)?)(?:\\[([^]]+)])?$").matcher(expression);
        if (!matcher.find()) {
            return 0.0;
        }
        double value = Double.parseDouble(matcher.group(1));
        String unit = matcher.group(2);
        if ("MPa".equals(unit)) return value * 1.0e6;
        if ("GPa".equals(unit)) return value * 1.0e9;
        return value;
    }

    private static void fillUpperTriangle(double[][] matrix, Map<String, Double> values) {
        int index = 0;
        for (int row = 0; row < 6; row++) {
            for (int col = row; col < 6; col++) {
                double value = values.get(D_KEYS[index++]);
                matrix[row][col] = value;
                matrix[col][row] = value;
            }
        }
    }

    private static void writeNodeResult(Path outputDir, NodeRun node, String payload) throws IOException {
        if (node.resultFile == null || node.resultFile.isEmpty()) {
            return;
        }
        Files.writeString(outputDir.resolve(node.resultFile), payload + "\n", StandardCharsets.UTF_8);
    }

    private static void writeWorkerOutputs(
        StepInput input,
        String stateInText,
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
        state.append("  \"materials_state\": ").append(objectJsonOrEmpty(stateInText, "materials_state")).append(",\n");
        state.append("  \"geometry_state\": ").append(objectJsonOrEmpty(stateInText, "geometry_state")).append(",\n");
        state.append("  \"history\": ").append(historyJson(stateInText, input, wafer, waferSkipped)).append("\n");
        state.append("}\n");
        Files.writeString(input.outputDir.resolve("state_out.json"), state.toString(), StandardCharsets.UTF_8);
    }

    private static String manifestToJson(StepInput input, Map<String, ManifestEntry> manifest) {
        StringBuilder builder = new StringBuilder();
        builder.append("{\n  \"step_id\": ").append(json(input.stepId)).append(",\n  \"nodes\": {");
        int index = 0;
        for (Map.Entry<String, ManifestEntry> entry : manifest.entrySet()) {
            if (index++ > 0) builder.append(",");
            builder.append("\n    ").append(json(entry.getKey())).append(": ").append(entry.getValue().toJson());
        }
        if (!manifest.isEmpty()) builder.append("\n  ");
        builder.append("}\n}\n");
        return builder.toString();
    }

    private static Map<String, RveResult> parseStateRves(String text) {
        Map<String, RveResult> values = new LinkedHashMap<>();
        String rveText = objectText(text, "rve");
        for (String object : splitTopLevelObjects(rveText)) {
            String key = objectKey(object);
            if (!key.isEmpty()) values.put(key, RveResult.fromJson(object));
        }
        return values;
    }

    private static String rveMapToJson(Map<String, RveResult> values) {
        StringBuilder json = new StringBuilder("{");
        int index = 0;
        for (Map.Entry<String, RveResult> entry : values.entrySet()) {
            if (index++ > 0) json.append(",");
            json.append("\n    ").append(json(entry.getKey())).append(": ").append(entry.getValue().toJson());
        }
        if (!values.isEmpty()) json.append("\n  ");
        json.append("}");
        return json.toString();
    }

    private static String objectJsonOrEmpty(String text, String key) {
        String object = objectText(text, key);
        return object.isEmpty() ? "{}" : "{" + object + "}";
    }

    private static String historyJson(String stateInText, StepInput input, WaferResult wafer, boolean waferSkipped) {
        String previous = arrayText(stateInText, "history");
        String entry = "{\"step_id\":" + json(input.stepId)
            + ",\"step_name\":" + json(input.stepName)
            + ",\"wafer_result\":" + wafer.toJson()
            + ",\"wafer_skipped\":" + waferSkipped
            + "}";
        if (previous.trim().isEmpty()) return "[" + entry + "]";
        return "[" + previous + "," + entry + "]";
    }

    private static final class StepInput {
        final String stepId;
        final String stepName;
        final boolean runWafer;
        final Path stateIn;
        final Path outputDir;
        final List<Path> parameterFiles;
        final List<NodeRun> nodes;

        private StepInput(String stepId, String stepName, boolean runWafer, Path stateIn, Path outputDir, List<Path> parameterFiles, List<NodeRun> nodes) {
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
                optionalStringValue(text, "step_name"),
                booleanValue(text, "run_wafer", true),
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
        final String source;
        final Map<String, String> studies;
        final Map<String, String> inputs;
        final List<Path> inputParameterTxtPaths;
        final Map<String, Path> inputParameterTxtPathBySlot;
        final Path outputTxtPath;

        private NodeRun(String id, String type, String action, String templateKey, String templatePath, String output, String resultFile, String source, Map<String, String> studies, Map<String, String> inputs, List<Path> inputParameterTxtPaths, Map<String, Path> inputParameterTxtPathBySlot, Path outputTxtPath) {
            this.id = id;
            this.type = type;
            this.action = action;
            this.templateKey = templateKey;
            this.templatePath = templatePath;
            this.output = output;
            this.resultFile = resultFile;
            this.source = source;
            this.studies = studies;
            this.inputs = inputs;
            this.inputParameterTxtPaths = inputParameterTxtPaths;
            this.inputParameterTxtPathBySlot = inputParameterTxtPathBySlot;
            this.outputTxtPath = outputTxtPath;
        }
    }

    private static List<NodeRun> parseNodes(String text) {
        Map<String, String> templateSpecs = rawTopLevelObjects(objectText(text, "template_specs"));
        List<NodeRun> nodes = new ArrayList<>();
        for (String nodeText : splitTopLevelArrayObjects(arrayText(text, "nodes"))) {
            String nodeId = optionalStringValue(nodeText, "node");
            if (nodeId.isEmpty()) nodeId = optionalStringValue(nodeText, "id");
            String templateKey = optionalStringValue(nodeText, "template");
            String specText = templateSpecs.getOrDefault(templateKey, "");
            String outputsText = objectText(specText, "outputs");
            Map<String, String> inputPaths = objectStrings(objectText(nodeText, "input_parameter_txt_paths"));
            List<Path> inputFiles = new ArrayList<>();
            Map<String, Path> inputFilesBySlot = new LinkedHashMap<>();
            for (Map.Entry<String, String> entry : inputPaths.entrySet()) {
                Path path = Path.of(entry.getValue());
                inputFiles.add(path);
                inputFilesBySlot.put(entry.getKey(), path);
            }
            String outputTxt = optionalStringValue(nodeText, "output_txt_path");
            nodes.add(
                new NodeRun(
                    nodeId,
                    optionalStringValue(nodeText, "type").isEmpty() ? nodeId : optionalStringValue(nodeText, "type"),
                    stringValue(nodeText, "action"),
                    templateKey,
                    optionalStringValue(specText, "path"),
                    optionalStringValue(nodeText, "output").isEmpty() ? ("wafer".equals(nodeId) ? "wafer_result" : "rve." + nodeId) : optionalStringValue(nodeText, "output"),
                    optionalStringValue(nodeText, "result_file").isEmpty() ? optionalStringValue(outputsText, "result_file") : optionalStringValue(nodeText, "result_file"),
                    optionalStringValue(nodeText, "source"),
                    objectStrings(objectText(specText, "studies")),
                    objectStrings(objectText(nodeText, "inputs")),
                    inputFiles,
                    inputFilesBySlot,
                    outputTxt.isEmpty() ? Path.of(optionalStringValue(outputsText, "txt_file")) : Path.of(outputTxt)
                )
            );
        }
        return nodes;
    }

    private static List<Path> parameterFilesInOrder(String text) {
        Map<String, String> paths = objectStrings(objectText(text, "parameter_txt_paths"));
        List<Path> files = new ArrayList<>();
        for (String key : stringArray(text, "parameter_txt_order")) {
            if (!paths.containsKey(key)) throw new IllegalArgumentException("Missing parameter_txt_paths entry for " + key);
            files.add(Path.of(paths.get(key)));
        }
        if (files.isEmpty()) for (String value : paths.values()) files.add(Path.of(value));
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

        static NodeResult rve(RveResult rve, ManifestEntry manifest) { return new NodeResult(rve, null, manifest); }
        static NodeResult wafer(WaferResult wafer, ManifestEntry manifest) { return new NodeResult(null, wafer, manifest); }
        static NodeResult skipped(ManifestEntry manifest) { return new NodeResult(null, null, manifest); }
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

        static ManifestEntry ran(NodeRun node, String sourceStep) { return new ManifestEntry(node.action, "success", node.templateKey, sourceStep, node.output); }
        static ManifestEntry inherited(NodeRun node, String sourceStep) { return new ManifestEntry(node.action, "success", node.templateKey, sourceStep, node.output); }
        static ManifestEntry skipped(NodeRun node) { return new ManifestEntry(node.action, "skipped", node.templateKey, "", node.output); }

        String toJson() {
            return "{\"action\":" + json(action)
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
        String sourceNode = "";
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

        RveResult copy() {
            RveResult copied = new RveResult(valid, active, rho, sxx, syy, d);
            copied.sourceStep = sourceStep;
            copied.sourceModel = sourceModel;
            copied.sourceNode = sourceNode;
            return copied;
        }

        static RveResult fromJson(String text) {
            RveResult rve = new RveResult(
                booleanValue(text, "valid", true),
                booleanValue(text, "active", true),
                doubleValue(text, "rho", 0.0),
                doubleValue(objectText(text, "stress_eff"), "sxx", 0.0),
                doubleValue(objectText(text, "stress_eff"), "syy", 0.0),
                matrix6x6FromJson(arrayText(text, "D"))
            );
            rve.sourceStep = optionalStringValue(text, "source_step");
            rve.sourceModel = optionalStringValue(text, "source_model");
            rve.sourceNode = optionalStringValue(text, "source_node");
            return rve;
        }

        String toJson() {
            return "{\"valid\":" + valid
                + ",\"active\":" + active
                + ",\"source_step\":" + json(sourceStep)
                + ",\"source_model\":" + json(sourceModel)
                + ",\"source_node\":" + json(sourceNode)
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
            if (wafer.isEmpty()) return skipped();
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

        static WaferResult skipped() { return new WaferResult(0.0, 0.0, 0.0, 0.0); }
    }

    private static String objectKey(String text) {
        Matcher matcher = Pattern.compile("^\\s*\"([^\"]+)\"\\s*:").matcher(text);
        return matcher.find() ? matcher.group(1) : "";
    }

    private static String objectText(String text, String key) {
        int keyIndex = text.indexOf("\"" + key + "\"");
        if (keyIndex < 0) return "";
        int start = text.indexOf("{", keyIndex);
        if (start < 0) return "";
        int end = matchingIndex(text, start, '{', '}');
        return end < 0 ? "" : text.substring(start + 1, end);
    }

    private static String arrayText(String text, String key) {
        int keyIndex = text.indexOf("\"" + key + "\"");
        if (keyIndex < 0) return "";
        int start = text.indexOf("[", keyIndex);
        if (start < 0) return "";
        int end = matchingIndex(text, start, '[', ']');
        return end < 0 ? "" : text.substring(start + 1, end);
    }

    private static int matchingIndex(String text, int start, char open, char close) {
        int depth = 0;
        boolean inString = false;
        boolean escaped = false;
        for (int i = start; i < text.length(); i++) {
            char ch = text.charAt(i);
            if (escaped) { escaped = false; continue; }
            if (ch == '\\') { escaped = true; continue; }
            if (ch == '"') { inString = !inString; continue; }
            if (inString) continue;
            if (ch == open) depth++;
            else if (ch == close) {
                depth--;
                if (depth == 0) return i;
            }
        }
        return -1;
    }

    private static List<String> splitTopLevelArrayObjects(String text) {
        List<String> values = new ArrayList<>();
        int index = 0;
        while (index < text.length()) {
            int start = text.indexOf("{", index);
            if (start < 0) break;
            int end = matchingIndex(text, start, '{', '}');
            if (end < 0) break;
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
            if (start < 0) continue;
            int end = matchingIndex(text, start, '{', '}');
            if (end < 0) continue;
            values.add(text.substring(matcher.start(), end + 1));
            matcher.region(end + 1, text.length());
        }
        return values;
    }

    private static Map<String, String> rawTopLevelObjects(String text) {
        Map<String, String> values = new LinkedHashMap<>();
        for (String object : splitTopLevelObjects(text)) {
            String key = objectKey(object);
            if (!key.isEmpty()) values.put(key, object.substring(object.indexOf("{")));
        }
        return values;
    }

    private static Map<String, String> objectStrings(String text) {
        Map<String, String> values = new LinkedHashMap<>();
        Matcher matcher = Pattern.compile("\"([^\"]+)\"\\s*:\\s*\"([^\"]*)\"").matcher(text);
        while (matcher.find()) values.put(matcher.group(1), matcher.group(2));
        return values;
    }

    private static List<String> stringArray(String text, String key) {
        Matcher matcher = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\\[([^]]*)]", Pattern.DOTALL).matcher(text);
        List<String> values = new ArrayList<>();
        if (matcher.find()) {
            Matcher item = Pattern.compile("\"([^\"]+)\"").matcher(matcher.group(1));
            while (item.find()) values.add(item.group(1));
        }
        return values;
    }

    private static String stringValue(String text, String key) {
        String value = optionalStringValue(text, key);
        if (value.isEmpty()) throw new IllegalArgumentException("Missing JSON string key: " + key);
        return value;
    }

    private static String optionalStringValue(String text, String key) {
        Matcher matcher = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\"([^\"]*)\"").matcher(text);
        return matcher.find() ? matcher.group(1) : "";
    }

    private static boolean booleanValue(String text, String key, boolean fallback) {
        Matcher matcher = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*(true|false)").matcher(text);
        return matcher.find() ? Boolean.parseBoolean(matcher.group(1)) : fallback;
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
            if (row > 0) builder.append(",");
            builder.append("[");
            for (int col = 0; col < matrix[row].length; col++) {
                if (col > 0) builder.append(",");
                builder.append(matrix[row][col]);
            }
            builder.append("]");
        }
        builder.append("]");
        return builder.toString();
    }

    private static String objectJsonOrEmpty(String text, String key) {
        String object = objectText(text, key);
        return object.isEmpty() ? "{}" : "{" + object + "}";
    }

    private static String json(String value) {
        if (value == null) return "\"\"";
        return "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }

    private static String sanitize(String value) {
        return value == null ? "" : value.replaceAll("[^A-Za-z0-9_]", "_");
    }
}
