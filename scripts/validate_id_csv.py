#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate app translation CSVs (frappe format: source,translation,context).

Checks:
1. File decodes as UTF-8 and parses cleanly with the csv module.
2. Every row has at least 2 columns; no empty source key.
3. No duplicate source keys.
4. Placeholder parity: {0}, {1}, ... / {named} present in the source must all
   appear in the translation and vice versa.
5. Hygiene: no CR characters, no leading/trailing whitespace on keys or
   translations (trailing-space variants like "Discount " are forbidden),
   no control characters.

Usage: python3 scripts/validate_id_csv.py [csv_path ...]
       (default: every pos_next/translations/*.csv)
"""
import csv
import re
import sys
from pathlib import Path

PLACEHOLDER = re.compile(r"\{[^{}]*\}")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def validate(path: Path) -> list[str]:
    errors = []
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return [f"{path}: not valid UTF-8: {e}"]

    if "\r" in text:
        errors.append(f"{path}: contains CR (carriage return) characters")

    rows = list(csv.reader(text.splitlines(True)))
    seen: dict[str, int] = {}
    for i, row in enumerate(rows, 1):
        where = f"{path}:{i}"
        if not row or all(not c.strip() for c in row):
            errors.append(f"{where}: empty row")
            continue
        if len(row) < 2:
            errors.append(f"{where}: expected >=2 columns, got {len(row)}: {row!r}")
            continue
        source, trans = row[0], row[1]
        if not source.strip():
            errors.append(f"{where}: empty source key")
            continue
        if source != source.strip() or trans != trans.strip():
            errors.append(f"{where}: leading/trailing whitespace in key or translation: {source!r}")
        if CONTROL.search(source) or CONTROL.search(trans):
            errors.append(f"{where}: control character in key or translation")
        if source in seen:
            errors.append(f"{where}: duplicate key {source!r} (first at row {seen[source]})")
        else:
            seen[source] = i
        ph_src = sorted(PLACEHOLDER.findall(source))
        ph_dst = sorted(PLACEHOLDER.findall(trans))
        if ph_src != ph_dst:
            errors.append(
                f"{where}: placeholder mismatch {source!r}: source {ph_src} vs translation {ph_dst}"
            )
    if text and not text.endswith("\n"):
        errors.append(f"{path}: missing final newline")
    return errors


def main() -> int:
    args = sys.argv[1:]
    base = Path(__file__).resolve().parent.parent / "pos_next" / "translations"
    paths = [Path(a) for a in args] if args else sorted(base.glob("*.csv"))
    if not paths:
        print(f"no csv files found under {base}")
        return 2
    errors: list[str] = []
    for p in paths:
        errors.extend(validate(p))
        n = len(list(csv.reader(p.read_text().splitlines(True))))
        print(f"{p}: {n} rows parsed")
    if errors:
        print(f"\nFAIL — {len(errors)} problem(s):")
        for e in errors:
            print(" ", e)
        return 1
    print("\nOK — all translation CSVs valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
