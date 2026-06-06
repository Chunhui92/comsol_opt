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

        writeWorkerOutputs(input, stateInText, rves, wafer, waferSkipped, manifest);
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
        injectRveInputs(model, node, rves);
        model.study(node.studies.getOrDefault("stress", DEFAULT_STUDY_TAG)).run();
        String cpStudy = node.studies.getOrDefault("cp", "");
        if (!cpStudy.isEmpty()) {
            model.study(cpStudy).run();
        }

        double rho = firstReal(model, node.extractorTags.getOrDefault("rho", DEFAULT_RHO_GEV_TAG), 0.0);
        double[] stress = realVector(model, node.extractorTags.getOrDefault("stress", DEFAULT_STRESS_GEV_TAG), 2);
        String matrixTag = node.extractorTags.getOrDefault("D", DEFAULT_D_MATRIX_TAG);
        double[][] stiffness = matrix6x6(model, matrixTag);

        ModelUtil.remove(model.tag());
        return new RveResult(true, true, rho, stress[0], stress[1], stiffness);
    }

    private static WaferResult runWaferModel(NodeRun node, List<Path> parameterFiles, Map<String, RveResult> rves) throws Exception {
        Model model = ModelUtil.load("model_wafer", node.templatePath);
        loadParameterFiles(model, parameterFiles);
        injectRveInputs(model, node, rves);
        model.study(node.studies.getOrDefault("stress", DEFAULT_STUDY_TAG)).run();

        double bowX = firstReal(model, node.extractorTags.getOrDefault("bow_x_um", DEFAULT_BOW_GEV_TAG), 0.0);
        double bowY = firstReal(model, node.extractorTags.getOrDefault("bow_y_um", DEFAULT_BOW_GEV_TAG), 0.0);
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

    private static void injectRveInputs(Model model, NodeRun node, Map<String, RveResult> rves) {
        for (InputTransfer transfer : node.inputTransfers) {
            if (!transfer.hasTargets()) {
                continue;
            }
            applyMaterialTarget(model, transfer.material, transfer);
            applyStressTarget(model, transfer.stress, transfer);
        }
        for (Map.Entry<String, String> input : node.inputs.entrySet()) {
            String sourceId = input.getValue().startsWith("rve.") ? input.getValue().substring("rve.".length()) : input.getValue();
            RveResult rve = rves.get(sourceId);
            if (rve == null || !rve.valid) {
                throw new IllegalStateException("Missing valid upstream RVE " + input.getValue());
            }
            String targetText = objectText(objectText(node.templateSpecText, "input_targets"), input.getKey());
            if (!targetText.isEmpty()) {
                InputTransfer transfer = InputTransfer.fromRve(input.getKey(), input.getValue(), rve);
                applyMaterialTarget(model, MaterialTarget.fromJson(objectText(targetText, "material")), transfer);
                applyStressTarget(model, StressTarget.fromJson(objectText(targetText, "stress")), transfer);
                continue;
            }
            String slot = sanitize(input.getKey());
            applyMaterialTarget(model, slot + "_component", slot + "_material", slot + "_elastic", "D", rve);
            applyStressTarget(model, slot + "_component", nodePhysicsTag(slot), slot + "_lemm", slot + "_initial_stress", rve);
        }
    }

    private static void applyMaterialTarget(Model model, MaterialTarget target, InputTransfer transfer) {
        model.component(target.componentTag)
            .material(target.materialTag)
            .propertyGroup(target.densityGroupTag)
            .set(target.densityPropertyName, new String[] { transfer.rhoExpression });
        model.component(target.componentTag)
            .material(target.materialTag)
            .propertyGroup(target.elasticGroupTag)
            .set(target.elasticPropertyName, transfer.dUpper21Expressions);
    }

    private static void applyStressTarget(Model model, StressTarget target, InputTransfer transfer) {
        model.component(target.componentTag)
            .physics(target.physicsTag)
            .feature(target.parentFeatureTag)
            .feature(target.featureTag)
            .set(target.propertyName, new String[] {
                transfer.sxxExpression, "0[Pa]", "0[Pa]",
                "0[Pa]", transfer.syyExpression, "0[Pa]",
                "0[Pa]", "0[Pa]", "0[Pa]"
            });
    }

    private static void applyMaterialTarget(
        Model model,
        String componentTag,
        String materialTag,
        String elasticGroupTag,
        String elasticPropertyName,
        RveResult rve
    ) {
        model.component(componentTag)
            .material(materialTag)
            .propertyGroup("def")
            .set("density", new String[] { rve.rho + "[kg/m^3]" });
        model.component(componentTag)
            .material(materialTag)
            .propertyGroup(elasticGroupTag)
            .set(elasticPropertyName, symmetricUpper21Expressions(rve));
    }

    private static void applyStressTarget(
        Model model,
        String componentTag,
        String physicsTag,
        String parentFeatureTag,
        String stressFeatureTag,
        RveResult rve
    ) {
        model.component(componentTag)
            .physics(physicsTag)
            .feature(parentFeatureTag)
            .feature(stressFeatureTag)
            .set("S0", new String[] {
                rve.sxx + "[Pa]", "0[Pa]", "0[Pa]",
                "0[Pa]", rve.syy + "[Pa]", "0[Pa]",
                "0[Pa]", "0[Pa]", "0[Pa]"
            });
    }

    private static String nodePhysicsTag(String slot) {
        return slot + "_physics";
    }

    private static String[] symmetricUpper21Expressions(RveResult rve) {
        List<String> values = new ArrayList<>();
        for (int row = 0; row < 6; row++) {
            for (int col = row; col < 6; col++) {
                values.add(rve.d[row][col] + "[Pa]");
            }
        }
        return values.toArray(new String[0]);
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
        state.append("  \"device_rves\": ").append(deviceRvesToJson(rves)).append(",\n");
        state.append("  \"wafer_inputs\": ").append(waferInputsToJson(rves, stateInText)).append(",\n");
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

    private static String deviceRvesToJson(Map<String, RveResult> rves) {
        StringBuilder json = new StringBuilder("{");
        int index = 0;
        if (rves.containsKey("device_main")) {
            json.append("\n    \"device_default\": ").append(rves.get("device_main").toJson());
            index++;
        }
        if (rves.containsKey("device_aux")) {
            if (index++ > 0) {
                json.append(",");
            }
            json.append("\n    \"device2_for_mat3\": ").append(rves.get("device_aux").toJson());
        }
        if (index > 0) {
            json.append("\n  ");
        }
        json.append("}");
        return json.toString();
    }

    private static String waferInputsToJson(Map<String, RveResult> rves, String stateInText) {
        String previous = objectText(stateInText, "wafer_inputs");
        Map<String, String> directInputs = rawTopLevelObjects(previous);
        StringBuilder json = new StringBuilder("{");
        int index = 0;
        for (Map.Entry<String, String> entry : directInputs.entrySet()) {
            if (index++ > 0) {
                json.append(",");
            }
            json.append("\n    ").append(json(entry.getKey())).append(": ").append(entry.getValue());
        }
        for (String key : new String[] {"mat1", "mat2", "mat3", "mat4", "die"}) {
            RveResult rve = rves.get(key);
            if (rve == null) {
                continue;
            }
            if (index++ > 0) {
                json.append(",");
            }
            json.append("\n    ").append(json(key)).append(": ").append(rve.toJson());
        }
        if (index > 0) {
            json.append("\n  ");
        }
        json.append("}");
        return json.toString();
    }

    private static String objectJsonOrEmpty(String text, String key) {
        String object = objectText(text, key);
        return object.isEmpty() ? "{}" : "{" + object + "}";
    }

    private static String historyJson(String stateInText, StepInput input, WaferResult wafer, boolean waferSkipped) {
        String history = arrayText(stateInText, "history");
        if (waferSkipped) {
            return history.isEmpty() ? "[]" : "[" + history + "]";
        }
        String entry = "{"
            + "\"step_id\":" + json(input.stepId)
            + ",\"step_name\":" + json(input.stepName)
            + ",\"bow_x_um\":" + wafer.bowX
            + ",\"bow_y_um\":" + wafer.bowY
            + "}";
        if (history.trim().isEmpty()) {
            return "[" + entry + "]";
        }
        return "[" + history + "," + entry + "]";
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
        final Map<String, String> studies;
        final Map<String, String> inputs;
        final List<InputTransfer> inputTransfers;
        final Map<String, String> extractorTags;
        final String templateSpecText;

        private NodeRun(
            String id,
            String type,
            String action,
            String templateKey,
            String templatePath,
            String output,
            String resultFile,
            Map<String, String> studies,
            Map<String, String> inputs,
            List<InputTransfer> inputTransfers,
            Map<String, String> extractorTags,
            String templateSpecText
        ) {
            this.id = id;
            this.type = type;
            this.action = action;
            this.templateKey = templateKey;
            this.templatePath = templatePath;
            this.output = output;
            this.resultFile = resultFile;
            this.studies = studies;
            this.inputs = inputs;
            this.inputTransfers = inputTransfers;
            this.extractorTags = extractorTags;
            this.templateSpecText = templateSpecText;
        }
    }

    private static List<NodeRun> parseNodes(String text) {
        List<NodeRun> nodes = new ArrayList<>();
        String array = arrayText(text, "runs");
        if (array.isEmpty()) {
            array = arrayText(text, "nodes");
        }
        Map<String, String> templateSpecs = rawTopLevelObjects(objectText(text, "template_specs"));
        for (String nodeText : splitTopLevelArrayObjects(array)) {
            String nodeId = optionalStringValue(nodeText, "node").isEmpty() ? optionalStringValue(nodeText, "id") : optionalStringValue(nodeText, "node");
            String nodeType = optionalStringValue(nodeText, "type").isEmpty() ? nodeId : optionalStringValue(nodeText, "type");
            String templateKey = optionalStringValue(nodeText, "template");
            String specText = templateSpecs.getOrDefault(templateKey, "");
            String outputsText = objectText(specText, "outputs");
            Map<String, String> specStudies = objectStrings(objectText(specText, "studies"));
            Map<String, String> specExtractors = objectStrings(objectText(specText, "extractors"));
            nodes.add(
                new NodeRun(
                    nodeId,
                    nodeType,
                    stringValue(nodeText, "action"),
                    templateKey,
                    optionalStringValue(nodeText, "template_path").isEmpty() ? optionalStringValue(specText, "path") : optionalStringValue(nodeText, "template_path"),
                    optionalStringValue(nodeText, "output").isEmpty() ? ("wafer".equals(nodeId) ? "wafer_result" : "rve." + nodeId) : optionalStringValue(nodeText, "output"),
                    optionalStringValue(nodeText, "result_file").isEmpty() ? optionalStringValue(outputsText, "result_file") : optionalStringValue(nodeText, "result_file"),
                    specStudies.isEmpty() ? objectStrings(objectText(nodeText, "studies")) : specStudies,
                    objectStrings(objectText(nodeText, "inputs")),
                    parseInputTransfers(nodeText),
                    specExtractors.isEmpty() ? objectStrings(objectText(nodeText, "extractors")) : specExtractors,
                    specText
                )
            );
        }
        return nodes;
    }

    private static List<InputTransfer> parseInputTransfers(String nodeText) {
        List<InputTransfer> transfers = new ArrayList<>();
        String inputsText = objectText(nodeText, "inputs");
        for (String slotObject : splitTopLevelObjects(inputsText)) {
            String slot = objectKey(slotObject);
            String rveText = objectText(slotObject, "rve");
            String targetText = objectText(slotObject, "target");
            if (slot.isEmpty() || rveText.isEmpty() || targetText.isEmpty()) {
                continue;
            }
            String dFormat = optionalStringValue(rveText, "D_format");
            String D_upper21 = "D_upper21";
            if (!"symmetric_upper21".equals(dFormat)) {
                throw new IllegalArgumentException("Input " + slot + " must use symmetric_upper21");
            }
            MaterialTarget material = MaterialTarget.fromJson(objectText(targetText, "material"));
            StressTarget stress = StressTarget.fromJson(objectText(targetText, "stress"));
            String stressText = objectText(rveText, "stress_eff");
            transfers.add(
                new InputTransfer(
                    slot,
                    stringValue(slotObject, "source"),
                    stringValue(rveText, "rho"),
                    stringValue(stressText, "sxx"),
                    stringValue(stressText, "syy"),
                    stringArray(rveText, D_upper21).toArray(new String[0]),
                    material,
                    stress
                )
            );
        }
        return transfers;
    }

    private static final class InputTransfer {
        final String slot;
        final String sourceRef;
        final String rhoExpression;
        final String sxxExpression;
        final String syyExpression;
        final String[] dUpper21Expressions;
        final MaterialTarget material;
        final StressTarget stress;

        private InputTransfer(
            String slot,
            String sourceRef,
            String rhoExpression,
            String sxxExpression,
            String syyExpression,
            String[] dUpper21Expressions,
            MaterialTarget material,
            StressTarget stress
        ) {
            this.slot = slot;
            this.sourceRef = sourceRef;
            this.rhoExpression = rhoExpression;
            this.sxxExpression = sxxExpression;
            this.syyExpression = syyExpression;
            this.dUpper21Expressions = dUpper21Expressions;
            this.material = material;
            this.stress = stress;
        }

        boolean hasTargets() {
            return material != null && stress != null;
        }

        static InputTransfer fromRve(String slot, String sourceRef, RveResult rve) {
            return new InputTransfer(
                slot,
                sourceRef,
                rve.rho + "[kg/m^3]",
                rve.sxx + "[Pa]",
                rve.syy + "[Pa]",
                symmetricUpper21Expressions(rve),
                null,
                null
            );
        }
    }

    private static final class MaterialTarget {
        final String componentTag;
        final String materialTag;
        final String densityGroupTag;
        final String densityPropertyName;
        final String elasticGroupTag;
        final String elasticPropertyName;

        private MaterialTarget(
            String componentTag,
            String materialTag,
            String densityGroupTag,
            String densityPropertyName,
            String elasticGroupTag,
            String elasticPropertyName
        ) {
            this.componentTag = componentTag;
            this.materialTag = materialTag;
            this.densityGroupTag = densityGroupTag;
            this.densityPropertyName = densityPropertyName;
            this.elasticGroupTag = elasticGroupTag;
            this.elasticPropertyName = elasticPropertyName;
        }

        static MaterialTarget fromJson(String text) {
            if (text.isEmpty()) {
                return null;
            }
            return new MaterialTarget(
                stringValue(text, "component"),
                stringValue(text, "tag"),
                optionalStringValue(text, "density_group").isEmpty() ? "def" : optionalStringValue(text, "density_group"),
                optionalStringValue(text, "density_property").isEmpty() ? "density" : optionalStringValue(text, "density_property"),
                stringValue(text, "elastic_group"),
                stringValue(text, "elastic_property")
            );
        }
    }

    private static final class StressTarget {
        final String componentTag;
        final String physicsTag;
        final String parentFeatureTag;
        final String featureTag;
        final String propertyName;

        private StressTarget(String componentTag, String physicsTag, String parentFeatureTag, String featureTag, String propertyName) {
            this.componentTag = componentTag;
            this.physicsTag = physicsTag;
            this.parentFeatureTag = parentFeatureTag;
            this.featureTag = featureTag;
            this.propertyName = propertyName;
        }

        static StressTarget fromJson(String text) {
            if (text.isEmpty()) {
                return null;
            }
            // target.stress
            return new StressTarget(
                stringValue(text, "component"),
                stringValue(text, "physics"),
                optionalStringValue(text, "parent_feature"),
                stringValue(text, "feature"),
                stringValue(text, "property")
            );
        }
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

    private static Map<String, String> rawTopLevelObjects(String text) {
        Map<String, String> values = new LinkedHashMap<>();
        for (String object : splitTopLevelObjects(text)) {
            String key = objectKey(object);
            if (!key.isEmpty()) {
                int start = object.indexOf("{");
                values.put(key, object.substring(start));
            }
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
