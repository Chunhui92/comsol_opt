CANONICAL_NODES = ("pillar", "sc", "decap1", "decap2", "decap3", "fecap", "die", "wafer", "onon")
CANONICAL_NODE_SET = set(CANONICAL_NODES)
RUN_ORDER = ("pillar", "sc", "decap1", "decap2", "decap3", "fecap", "die", "wafer")
CHAIN_INPUTS = {
    "decap1": ("pillar", "onon"),
    "decap2": ("pillar", "onon"),
    "decap3": ("pillar", "onon"),
    "fecap": ("pillar", "sc", "onon"),
    "die": ("decap1", "decap2", "decap3", "fecap", "onon"),
    "wafer": ("die", "onon"),
}

LEGACY_NODE_NAMES = {
    "device",
    "device_main",
    "device_aux",
    "onon_device",
    "mat1",
    "mat2",
    "mat3",
    "mat4",
    "cap1",
    "cap2",
    "cap3",
    "cap4",
}

V2_ACTIONS = {"run", "inherit", "default_from", "alias"}
RVE_PREFIX = "rve."


def matrix_to_upper21(matrix):
    _validate_matrix6x6(matrix)
    values = []
    for row in range(6):
        for col in range(row, 6):
            values.append(float(matrix[row][col]))
    return values


def upper21_to_matrix(values):
    if not isinstance(values, list) or len(values) != 21:
        raise ValueError("upper-triangular D payload must contain exactly 21 values")
    matrix = [[0.0] * 6 for _ in range(6)]
    index = 0
    for row in range(6):
        for col in range(row, 6):
            value = float(values[index])
            matrix[row][col] = value
            matrix[col][row] = value
            index += 1
    return matrix


def canonical_ref_name(ref):
    if not isinstance(ref, str) or not ref.startswith(RVE_PREFIX):
        raise ValueError(f"RVE reference must use rve.<node>: {ref}")
    name = ref[len(RVE_PREFIX):]
    if name not in CANONICAL_NODE_SET:
        raise ValueError(f"Unknown canonical RVE reference: {ref}")
    return name


def _validate_matrix6x6(matrix):
    if not isinstance(matrix, list) or len(matrix) != 6:
        raise ValueError("D must be a 6x6 matrix")
    for row_index, row in enumerate(matrix):
        if not isinstance(row, list) or len(row) != 6:
            raise ValueError("D must be a 6x6 matrix")
        for col_index, value in enumerate(row):
            float(value)
            if row_index > col_index and float(value) != float(matrix[col_index][row_index]):
                raise ValueError("D must be symmetric")
