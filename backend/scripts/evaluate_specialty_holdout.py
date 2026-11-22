"""Evaluate a de-identified, clinician-labeled specialty holdout JSON file."""
import json
from pathlib import Path

from app.specialty_validation import evaluate_specialty_holdout

if __name__ == "__main__":
    report = evaluate_specialty_holdout()
    print(json.dumps(report, indent=2))
    if report["sample_count"] == 0:
        print("Suggestions remain disabled: no clinician-labeled holdout cases were supplied.")
