from types import SimpleNamespace
import uuid

from app import db


class _Query:
    def __init__(self, client, table):
        self.client = client
        self.table_name = table
        self.filters = {}
        self.inserted = None

    def select(self, *_args):
        return self

    def insert(self, record):
        self.inserted = dict(record)
        return self

    def upsert(self, record, **_kwargs):
        self.inserted = dict(record)
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        rows = self.client.rows.setdefault(self.table_name, [])
        if self.inserted is not None:
            rows.append(self.inserted)
            return SimpleNamespace(data=[self.inserted])
        found = [row for row in rows if all(row.get(k) == v for k, v in self.filters.items())]
        return SimpleNamespace(data=found)


class _Client:
    def __init__(self):
        self.rows = {}

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params):
        assert name == "save_doctor_action_atomic"
        return _Rpc(self, params)


class _Rpc:
    def __init__(self, client, params):
        self.client = client
        self.params = params

    def execute(self):
        rows = self.client.rows.setdefault("doctor_actions", [])
        fingerprint = self.params["p_fingerprint"]
        existing = next((row for row in rows if row.get("idempotency_fingerprint") == fingerprint), None)
        if existing:
            return SimpleNamespace(data={"duplicate": True, "row": existing})
        row = dict(self.params["p_action"])
        rows.append(row)
        return SimpleNamespace(data={"duplicate": False, "row": row})


def test_consultation_prescriptions_are_encrypted_and_round_trip():
    data = {"prescriptions": [{"medication_name": "Example", "dosage": "10 mg"}]}
    encoded = db._encode_consultation_row(data)
    assert encoded["prescriptions"].get("_phi_ciphertext_v2")
    assert db._decode_consultation_row(encoded)["prescriptions"] == data["prescriptions"]


def test_doctor_action_persists_and_reads_complete_details(monkeypatch):
    client = _Client()
    monkeypatch.setattr(db, "get_supabase_client", lambda: client)
    action = {
        "action_id": "ACT-test-123",
        "consultation_id": str(uuid.uuid4()),
        "doctor_id": str(uuid.uuid4()),
        "doctor_registration_number": "REG-123",
        "state_medical_council": "State Council",
        "action_type": "PRESCRIBE_MEDICATION",
        "clinical_notes": "Clinical rationale with patient-specific details.",
        "new_priority": None,
        "referral_hospital": None,
        "referral_urgency": None,
        "prescriptions": [{"medication_name": "Example", "dosage": "10 mg"}],
        "timestamp": 1763700000.0,
    }

    db.save_doctor_action(action)
    stored = client.rows["doctor_actions"][0]
    assert stored["notes"].startswith("v2:")
    assert stored["details"].get("_phi_ciphertext_v2")
    assert db.get_doctor_actions(action["consultation_id"]) == [action]

    retry = {**action, "action_id": "ACT-new-retry", "timestamp": action["timestamp"] + 1}
    duplicate = db.save_doctor_action(retry)
    assert duplicate["_duplicate"] is True
    assert duplicate["action_id"] == action["action_id"]
    assert len(client.rows["doctor_actions"]) == 1


def test_guest_case_phi_is_ciphertext_in_database_and_decoded_for_application(monkeypatch):
    client = _Client()
    monkeypatch.setattr(db, "get_supabase_client", lambda: client)
    case_id = str(uuid.uuid4())
    source = {
        "patient_name": "Private Name",
        "age": 12,
        "chief_complaint": "Private clinical history",
        "urgency_tier": "emergency",
        "latitude": 12.4,
        "longitude": 77.6,
        "guest_token_hash": "hash",
    }

    db.save_guest_case(case_id, source)
    stored = client.rows["guest_cases"][0]
    assert stored["patient_name"].startswith("v2:")
    assert stored["chief_complaint"].startswith("v2:")
    assert stored["age"] is None
    assert stored["age_ciphertext"].startswith("v2:")
    assert "latitude" not in stored and "longitude" not in stored
    assert db.get_guest_case(case_id)["age"] == 12
    assert db.get_guest_case(case_id)["latitude"] == 12.4


def test_sos_coordinates_and_deletion_metadata_are_encrypted(monkeypatch):
    client = _Client()
    monkeypatch.setattr(db, "get_supabase_client", lambda: client)

    db.save_sos_dispatch({"patient_name": "Private Name", "latitude": 12.4, "longitude": 77.6})
    sos_row = client.rows["emergency_sos_dispatches"][0]
    assert sos_row["patient_name"].startswith("v2:")
    assert sos_row["latitude"] is None and sos_row["longitude"] is None
    assert sos_row["location_ciphertext"].startswith("v2:")
    sos_result = db.get_sos_dispatches()[0]
    assert sos_result["latitude"] == 12.4 and sos_result["longitude"] == 77.6

    db.log_deletion_event("audio", "private/path.wav", "Private retention detail", metadata={"note": "private"})
    deletion_row = client.rows["deletion_log"][0]
    assert deletion_row["resource_id"].startswith("v2:")
    assert deletion_row["reason"].startswith("v2:")
    assert deletion_row["metadata"].get("_phi_ciphertext_v2")
    assert db.get_deletion_logs()[0]["resource_id"] == "private/path.wav"


def test_assignment_and_audio_retention_encrypt_identifying_fields(monkeypatch):
    from app.security.data_retention import DataRetentionManager

    client = _Client()
    monkeypatch.setattr(db, "get_supabase_client", lambda: client)
    consultation_id = str(uuid.uuid4())
    doctor_id = str(uuid.uuid4())
    assignment = db.save_assignment(
        consultation_id,
        doctor_id,
        reassignment_reason="Private clinical reason",
        locked_by_name="Doctor Name",
    )
    stored_assignment = client.rows["consultation_assignments"][0]
    assert stored_assignment["reassignment_reason"].startswith("v2:")
    assert stored_assignment["locked_by_name"].startswith("v2:")
    fetched_assignment = db.get_assignment_for_consultation(consultation_id)
    assert fetched_assignment["reassignment_reason"] == assignment["reassignment_reason"]
    assert fetched_assignment["locked_by_name"] == assignment["locked_by_name"]

    DataRetentionManager().schedule_audio_deletion("private/case-audio.wav", hours=1)
    stored_audio = client.rows["audio_retention_queue"][0]
    assert stored_audio["file_path"].startswith("v2:")
    assert stored_audio["file_path_lookup_hash"]
