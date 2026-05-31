from pathlib import Path


class ParameterTxtSet:
    """Update COMSOL parameter txt files while preserving simple line style."""

    def __init__(self, files, mappings):
        self.files = {key: Path(value) for key, value in files.items()}
        self.mappings = mappings

    def write_trial_files(self, output_dir, values):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_paths = {}
        for group, src in self.files.items():
            dst = output_dir / src.name
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            output_paths[group] = dst

        by_file = {}
        for dotted_key, value in values.items():
            mapping = self.mappings.get(dotted_key)
            if mapping is None:
                continue
            by_file.setdefault(mapping["file"], {})[mapping["name"]] = value

        for group, updates in by_file.items():
            path = output_paths[group]
            path.write_text(_update_parameter_text(path.read_text(encoding="utf-8"), updates), encoding="utf-8")
        return output_paths


def _format_value(value):
    return str(value)


def _update_parameter_text(text, updates):
    remaining = dict(updates)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("%"):
            lines.append(line)
            continue
        if "=" in line:
            left, _right = line.split("=", 1)
            name = left.strip()
            if name in remaining:
                lines.append(f"{left.rstrip()} = {_format_value(remaining.pop(name))}")
            else:
                lines.append(line)
            continue
        parts = line.split()
        name = parts[0]
        if name in remaining:
            lines.append(f"{name} {_format_value(remaining.pop(name))}")
        else:
            lines.append(line)
    for name, value in remaining.items():
        lines.append(f"{name} = {_format_value(value)}")
    return "\n".join(lines) + "\n"

