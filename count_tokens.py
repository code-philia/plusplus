#!/usr/bin/env python3
"""Sum MODEL_USAGE total_tokens in a log and print millions of tokens."""

import argparse
import json
import re
from decimal import Decimal
from pathlib import Path


def sum_tokens(path: Path) -> int:
    total = 0
    decoder = json.JSONDecoder()
    with path.open(encoding="utf-8", errors="replace") as log:
        for line_number, line in enumerate(log, start=1):
            for match in re.finditer(r"\bMODEL_USAGE\s+(?=\{)", line):
                try:
                    usage, _ = decoder.raw_decode(line[match.end():])
                    tokens = usage["total_tokens"]
                    if type(tokens) is not int or tokens < 0:
                        raise ValueError("total_tokens must be a nonnegative integer")
                except (ValueError, KeyError, TypeError) as exc:
                    raise ValueError(f"Invalid MODEL_USAGE at line {line_number}: {exc}") from exc
                total += tokens
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_path", type=Path, help="Path to the .log file")
    args = parser.parse_args()
    try:
        total = sum_tokens(args.log_path.expanduser())
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(f"{Decimal(total) / Decimal(1_000_000):.6f} M")


if __name__ == "__main__":
    main()
