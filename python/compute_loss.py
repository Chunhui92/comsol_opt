#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from comsol_opt.loss import compute_loss_from_rows, load_summary_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", required=True)
    parser.add_argument("--scale-x", type=float, default=30.0)
    parser.add_argument("--scale-y", type=float, default=30.0)
    args = parser.parse_args()
    result = compute_loss_from_rows(load_summary_rows(args.summary), args.scale_x, args.scale_y)
    print(f"loss={result['loss']:.6g} n_steps={result['n_steps']}")


if __name__ == "__main__":
    main()
