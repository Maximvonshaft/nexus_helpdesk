#!/usr/bin/env python3
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "02_problem_definition_evidence/traceability_matrix.csv"
with path.open(encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
print("# Traceability Summary")
for row in rows:
    print(f"- {row['Requirement']} -> {row['Implementation Files']} -> {row['Tests']} -> {row['Acceptance']}")
