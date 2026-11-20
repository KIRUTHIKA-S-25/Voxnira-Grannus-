"""
Administrative Staff Provisioning & Password Rotation Script for Grannus RuralCare AI.

Compliant with Security Audit A2 & Telemedicine Practice Guidelines:
  - Generates unique, cryptographically random 24-character passwords for all clinical staff.
  - Stores PBKDF2-HMAC-SHA256 salt-hashed credentials into database `staff_users` table.
  - Rotates credentials and eliminates static passwords from source code.
  - Never stores plain passwords in database or code repository.
  - Outputs generated credentials into a secured local credentials file (default: credentials_manifest.json).

Usage:
    cd backend
    python -m scripts.seed_staff
    # or with custom hospital ID:
    python -m scripts.seed_staff --hospital-id <UUID>
"""
import os
import sys
import json
import uuid
import secrets
import argparse
from datetime import datetime, timezone
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.config import get_settings
from app.auth import hash_password
from app.db import get_supabase_client

# Accredited Staff Directory Specification
STAFF_ROSTER = [
    {
        "email": "dr.rajan@hospital.in",
        "full_name": "Dr. Rajan K., MD",
        "role": "doctor",
        "qualifications": "MBBS, MD (General Medicine)",
        "doctor_registration_number": "TNMC-48291",
        "state_council": "Tamil Nadu Medical Council",
        "specialty": "General Medicine",
        "department_code": "GEN_MED",
        "opd_room": "Room 101, Ground Floor OPD Block",
        "experience": "16 Years Clinical Practice",
        "languages": ["ta", "en", "hi"],
        "max_capacity": 20,
    },
    {
        "email": "dr.priya@hospital.in",
        "full_name": "Dr. Priya Sundaram, DM",
        "role": "doctor",
        "qualifications": "MBBS, MD, DM (Cardiology), FACC",
        "doctor_registration_number": "TNMC-54321",
        "state_council": "Tamil Nadu Medical Council",
        "specialty": "Cardiology",
        "department_code": "CARDIOLOGY",
        "opd_room": "Room 201, 2nd Floor Cardiology Wing",
        "experience": "13 Years Clinical Practice",
        "languages": ["ta", "en"],
        "max_capacity": 10,
    },
    {
        "email": "dr.harish@hospital.in",
        "full_name": "Dr. Harish Naidu, MBBS, MEM",
        "role": "doctor",
        "qualifications": "MBBS, MEM (Emergency Medicine), ACLS Certified",
        "doctor_registration_number": "TNMC-72019",
        "state_council": "Tamil Nadu Medical Council",
        "specialty": "Emergency & Acute Care",
        "department_code": "EMERGENCY",
        "opd_room": "Resuscitation Bay 1, Casualty Emergency Block",
        "experience": "9 Years Emergency & Trauma Practice",
        "languages": ["ta", "hi", "te", "en"],
        "max_capacity": 25,
    },
    {
        "email": "dr.meenakshi@hospital.in",
        "full_name": "Dr. Meenakshi Iyer, MS",
        "role": "doctor",
        "qualifications": "MBBS, MS (Obstetrics & Gynecology), DGO",
        "doctor_registration_number": "TNMC-61245",
        "state_council": "Tamil Nadu Medical Council",
        "specialty": "Obstetrics & Gynecology",
        "department_code": "OBGYN",
        "opd_room": "Room 108, Maternal & Antenatal Care Block",
        "experience": "15 Years Clinical Practice",
        "languages": ["ta", "en"],
        "max_capacity": 12,
    },
    {
        "email": "dr.vikram@hospital.in",
        "full_name": "Dr. Vikram Patel, MD",
        "role": "doctor",
        "qualifications": "MBBS, MD (Pediatrics), DNB",
        "doctor_registration_number": "GMC-39104",
        "state_council": "Gujarat Medical Council",
        "specialty": "Pediatrics",
        "department_code": "PEDIATRICS",
        "opd_room": "Room 105, Child Health & Immunization Clinic",
        "experience": "11 Years Clinical Practice",
        "languages": ["hi", "en", "te"],
        "max_capacity": 15,
    },
    {
        "email": "dr.arvind@hospital.in",
        "full_name": "Dr. Arvind Kumar, MS",
        "role": "doctor",
        "qualifications": "MBBS, MS (Orthopaedics), MCh",
        "doctor_registration_number": "DMC-50123",
        "state_council": "Delhi Medical Council",
        "specialty": "Orthopaedics",
        "department_code": "ORTHOPEDICS",
        "opd_room": "Room 112, Orthopaedic Trauma & Fracture Clinic",
        "experience": "12 Years Clinical Practice",
        "languages": ["hi", "ta", "en"],
        "max_capacity": 15,
    },
    {
        "email": "dr.ananya@hospital.in",
        "full_name": "Dr. Ananya Mukherjee, MD, DM",
        "role": "doctor",
        "qualifications": "MBBS, MD (Respiratory Medicine), DM",
        "doctor_registration_number": "WBMC-58210",
        "state_council": "West Bengal Medical Council",
        "specialty": "Pulmonology",
        "department_code": "PULMONOLOGY",
        "opd_room": "Room 204, Chest Clinic & Pulmonary Lab",
        "experience": "12 Years Clinical Practice",
        "languages": ["hi", "en", "ta"],
        "max_capacity": 15,
    },
    {
        "email": "dr.karthik@hospital.in",
        "full_name": "Dr. Karthik Narayanan, MD, DM",
        "role": "doctor",
        "qualifications": "MBBS, MD, DM (Neurology)",
        "doctor_registration_number": "TNMC-79401",
        "state_council": "Tamil Nadu Medical Council",
        "specialty": "Neurology",
        "department_code": "NEUROLOGY",
        "opd_room": "Room 208, Stroke & Neurological Care Unit",
        "experience": "15 Years Clinical Practice",
        "languages": ["ta", "en", "te"],
        "max_capacity": 12,
    },
    {
        "email": "dr.deepa@hospital.in",
        "full_name": "Dr. Deepa Shenoy, MD, DM",
        "role": "doctor",
        "qualifications": "MBBS, MD, DM (Medical Gastroenterology)",
        "doctor_registration_number": "KMC-46190",
        "state_council": "Karnataka Medical Council",
        "specialty": "Gastroenterology",
        "department_code": "GASTROENTEROLOGY",
        "opd_room": "Room 210, Digestive Diseases OPD",
        "experience": "13 Years Clinical Practice",
        "languages": ["en", "hi", "ta"],
        "max_capacity": 15,
    },
    {
        "email": "dr.shalini@hospital.in",
        "full_name": "Dr. Shalini Reddy, MD",
        "role": "doctor",
        "qualifications": "MBBS, MD (DVL - Dermatology)",
        "doctor_registration_number": "APMC-28491",
        "state_council": "Andhra Pradesh Medical Council",
        "specialty": "Dermatology",
        "department_code": "DERMATOLOGY",
        "opd_room": "Room 115, Cutaneous & Skin Care Clinic",
        "experience": "10 Years Clinical Practice",
        "languages": ["te", "en"],
        "max_capacity": 15,
    },
    {
        "email": "dr.suresh@hospital.in",
        "full_name": "Dr. Suresh Varma, MS",
        "role": "doctor",
        "qualifications": "MBBS, MS (ENT - Otorhinolaryngology)",
        "doctor_registration_number": "TSMC-33109",
        "state_council": "Telangana State Medical Council",
        "specialty": "ENT",
        "department_code": "ENT",
        "opd_room": "Room 118, ENT & Audiology Suite",
        "experience": "14 Years Clinical Practice",
        "languages": ["te", "ta", "en"],
        "max_capacity": 15,
    },
    {
        "email": "dr.senthil@hospital.in",
        "full_name": "Dr. Senthil Nathan, MS, DO",
        "role": "doctor",
        "qualifications": "MBBS, MS (Ophthalmology), DO",
        "doctor_registration_number": "TNMC-65912",
        "state_council": "Tamil Nadu Medical Council",
        "specialty": "Ophthalmology",
        "department_code": "OPHTHALMOLOGY",
        "opd_room": "Room 120, Eye Care OPD & Refraction Unit",
        "experience": "17 Years Clinical Practice",
        "languages": ["ta", "en"],
        "max_capacity": 20,
    },
    {
        "email": "admin@hospital.in",
        "full_name": "Medical Superintendent",
        "role": "admin",
        "qualifications": "MS (Gen Surg), Hospital Administration",
        "doctor_registration_number": "ADMIN-TN-01",
        "state_council": "Tamil Nadu Medical Council",
        "specialty": "Administration & Hospital Operations",
        "department_code": "GEN_MED",
        "languages": ["ta", "en"],
        "max_capacity": 50,
    },
    {
        "email": "admin_user@hospital.in",
        "full_name": "System Administrator",
        "role": "admin",
        "qualifications": "Health Informatics & System Security",
        "doctor_registration_number": None,
        "state_council": None,
        "specialty": "IT & Infrastructure",
        "department_code": "GEN_MED",
        "languages": ["en"],
        "max_capacity": 50,
    },
    {
        "email": "nurse.mary@hospital.in",
        "full_name": "Staff Nurse Deepa R.",
        "role": "nurse",
        "qualifications": "B.Sc. Nursing, Emergency Triage Certified",
        "doctor_registration_number": "TNC-NURSE-042",
        "state_council": "Tamil Nadu Nursing Council",
        "specialty": "Triage & Vitals",
        "department_code": "EMERGENCY",
        "languages": ["ta", "en"],
        "max_capacity": 30,
    },
]


def generate_secure_password(length: int = 24) -> str:
    """Generate cryptographically strong, URL-safe random password."""
    return secrets.token_urlsafe(length)[:length]


def seed_staff_accounts(hospital_id: str = "c5b971d1-fe39-40bf-a5cb-539f1a98059f", output_file: str = "credentials_manifest.json"):
    client = get_supabase_client()
    generated_credentials = []

    print("=" * 70)
    print("Grannus RuralCare AI — Staff Provisioning & Password Rotation")
    print("=" * 70)
    print(f"Hospital Facility ID: {hospital_id}")
    print(f"Database Target:      {'Supabase Connected' if client else 'In-Memory Fallback'}")
    print("-" * 70)

    for staff in STAFF_ROSTER:
        email = staff["email"].strip().lower()
        raw_password = generate_secure_password(24)
        pw_hash = hash_password(raw_password)
        user_id = str(uuid.uuid4())

        record = {
            "id": user_id,
            "email": email,
            "password_hash": pw_hash,
            "role": staff["role"],
            "full_name": staff["full_name"],
            "doctor_registration_number": staff.get("doctor_registration_number"),
            "state_council": staff.get("state_council"),
            "specialty": staff.get("specialty"),
            "department_code": staff.get("department_code", "GEN_MED"),
            "languages": staff.get("languages", ["ta", "en"]),
            "hospital_id": hospital_id,
            "is_active": True,
            "is_on_duty": True,
            "is_verified_doctor": bool(staff.get("doctor_registration_number")),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Persist into database staff_users table
        if client:
            try:
                client.table("staff_users").upsert(record).execute()
                status = "DB_PERSISTED"
            except Exception as exc:
                status = f"DB_ERROR: {exc}"
        else:
            status = "FALLBACK_READY"

        generated_credentials.append({
            "email": email,
            "full_name": staff["full_name"],
            "role": staff["role"],
            "temporary_password": raw_password,
            "specialty": staff.get("specialty"),
            "registration_number": staff.get("doctor_registration_number"),
            "status": status,
        })
        print(f"[{status:^14}] {email} ({staff['role']}) -> {staff['full_name']}")

    # Save manifest for administrative distribution
    out_path = Path(output_file).resolve()
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "hospital_id": hospital_id,
                "note": "CONFIDENTIAL: Distribute credentials securely to verified medical staff. Require password change upon initial login.",
                "credentials": generated_credentials,
            },
            f,
            indent=2,
        )

    print("-" * 70)
    print(f"Provisioned {len(generated_credentials)} accounts.")
    print(f"Confidential manifest written to: {out_path}")
    print("=" * 70)
    return generated_credentials


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed staff accounts with random passwords")
    parser.add_argument("--hospital-id", default="c5b971d1-fe39-40bf-a5cb-539f1a98059f", help="Target hospital facility UUID")
    parser.add_argument("--output", default="credentials_manifest.json", help="Output path for credential manifest")
    args = parser.parse_args()

    seed_staff_accounts(hospital_id=args.hospital_id, output_file=args.output)
