"""Compare an export file of the demo orders with the answer keys' expected rows.

    make check-export FILE=~/Downloads/DemoCleaning_orders_2026-10-23_1415.csv

The expected file is every exportable demo order's expected rows, oldest order
first, under the SalesOrderImport_example.csv header. Exits 1 on any difference.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

from pipeline.tables import decode_csv

HEADER = ["AccountRef", "CustOrderNo", "OrderDate", "StockCode", "Qty", "UnitPrice", "DeliveryDate", "Notes"]


def expected_rows(answers_dir: Path) -> list[list[str]]:
    answers = [json.loads(p.read_text(encoding="utf-8")) for p in answers_dir.glob("*.json")]
    answers.sort(key=lambda a: a["received_at"])
    return [row for a in answers for row in a["expected_export_rows"]]


def file_rows(data: bytes) -> tuple[list[str], list[list[str]], str]:
    text, _encoding, delimiter = decode_csv(data)
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    return (rows[0] if rows else []), rows[1:], delimiter


def differences(data: bytes, answers_dir: Path) -> list[str]:
    header, rows, delimiter = file_rows(data)
    expected = expected_rows(answers_dir)
    diffs = []
    if delimiter != ",":
        diffs.append(f"delimiter is {delimiter!r}, expected ','")
    if header != HEADER:
        diffs.append(f"header is {header}, expected {HEADER}")
    if b"\r\n" not in data:
        diffs.append("lines do not end in CRLF")
    if len(rows) != len(expected):
        diffs.append(f"{len(rows)} rows, expected {len(expected)}")
    for n, (mine, theirs) in enumerate(zip(rows, expected), start=2):
        if mine != theirs:
            diffs.append(f"row {n}: {','.join(mine)}  (expected {','.join(theirs)})")
    return diffs


def main(argv: list[str] | None = None) -> int:
    from seed.demo_data import SEED_DIR

    parser = argparse.ArgumentParser(description="Check a demo export file against the answer keys")
    parser.add_argument("file", type=Path)
    parser.add_argument("--answers", type=Path, default=SEED_DIR / "answers")
    args = parser.parse_args(argv)
    if not args.answers.is_dir():
        print("The demo answer keys are missing. Run make seed first.")
        return 2
    diffs = differences(args.file.expanduser().read_bytes(), args.answers)
    expected = len(expected_rows(args.answers))
    if diffs:
        print(f"{args.file.name} does not match the {expected} expected rows:")
        for d in diffs:
            print("  " + d)
        return 1
    print(f"{args.file.name} matches the answer keys exactly: {expected} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
