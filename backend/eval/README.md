# Specialty suggestion holdout

Suggestions stay disabled unless `specialty_holdout.json` contains a complete, de-identified, clinician-labeled holdout whose top-1 department accuracy is at least 90%.

Each row must include:

- `deidentified: true`
- `clinician_labeled: true`
- `chief_complaint`: de-identified symptom summary
- `symptoms`: list of symptom strings or `{ "name": "...", "negated": false }` objects
- `urgency_tier`: the triage urgency used by routing
- `expected_specialty`: clinician-confirmed department code

Do not include names, contact information, account/profile/patient IDs, or other direct identifiers. Run `python -m scripts.evaluate_specialty_holdout` from `backend/`; an empty or incomplete file fails closed. Do not add synthetic labels or use model-generated labels as clinician ground truth.
