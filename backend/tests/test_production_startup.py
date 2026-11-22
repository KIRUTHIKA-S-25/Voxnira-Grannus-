from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.main as main
from app.main import _validate_production_settings, _is_deployed_https_origin
from app.config import Settings


def _production_settings(**overrides):
    values = {
        "env": "production",
        "is_production": True,
        "supabase_url": "https://cvnuaxcyadfycsfkeftu.supabase.co",
        "supabase_service_role_key": "service-role-live-value",
        "default_hospital_id": "c5b971d1-fe39-40bf-a5cb-539f1a98059f",
        "sarvam_api_key": "sarvam-live-value",
        "gemini_api_key": "gemini-live-value",
        "jwt_secret_key": "j" * 48,
        "phi_encryption_key": "p" * 48,
        "audio_retention_hours": 72,
        "allow_dev_otp": False,
        "grievance_officer_name": "Privacy Officer",
        "grievance_officer_email": "privacy@ruralcare.in",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_production_settings_accept_valid_required_configuration(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://care.ruralcare.in,https://staff.ruralcare.in")
    _validate_production_settings(_production_settings())


def test_production_settings_reject_missing_ai_credentials(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://care.ruralcare.in")
    with pytest.raises(RuntimeError, match="SARVAM_API_KEY"):
        _validate_production_settings(_production_settings(sarvam_api_key=""))


def test_production_settings_require_a_valid_default_hospital(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://care.ruralcare.in")
    with pytest.raises(RuntimeError, match="DEFAULT_HOSPITAL_ID"):
        _validate_production_settings(_production_settings(default_hospital_id=""))
    with pytest.raises(RuntimeError, match="DEFAULT_HOSPITAL_ID"):
        _validate_production_settings(_production_settings(default_hospital_id="not-a-uuid"))


def test_production_default_hospital_lookup_never_uses_demo_fallback(monkeypatch):
    import app.db as db

    class Query:
        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def limit(self, *_args):
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    class Client:
        def table(self, _name):
            return Query()

    monkeypatch.setattr(db, "get_settings", lambda: _production_settings())
    monkeypatch.setattr(db, "get_supabase_client", lambda: Client())
    with pytest.raises(RuntimeError, match="unavailable in Supabase"):
        db.get_default_hospital()


def test_production_consultation_write_rejects_unconfigured_hospital(monkeypatch):
    import app.db as db

    monkeypatch.setattr(db, "get_settings", lambda: SimpleNamespace(default_hospital_id="", is_production=True))
    monkeypatch.setattr(db, "get_supabase_client", lambda: None)
    with pytest.raises(RuntimeError, match="configured hospital ID"):
        db.save_consultation("2ed54fa2-5c9f-47bb-a4a3-81dd6af1b92b", {})


@pytest.mark.parametrize("origin", [
    "http://care.ruralcare.in",
    "https://care.ruralcare.in/path",
    "https://localhost:3000",
    "https://127.0.0.1",
    "https://care.ruralcare.in?x=1",
    "https://user@care.ruralcare.in",
    "https://care.ruralcare.in:bad",
])
def test_production_cors_origins_must_be_deployed_https_origins(origin):
    assert not _is_deployed_https_origin(origin)


def test_production_settings_reject_invalid_cors_origin(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://care.ruralcare.in/path")
    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS"):
        _validate_production_settings(_production_settings())


@pytest.mark.parametrize("env", ["production", "PRODUCTION", "prod", "staging"])
def test_supported_production_environment_labels_share_secure_mode(env):
    assert Settings(env=env).is_production is True


@pytest.mark.parametrize("env", ["development", "test"])
def test_development_environment_labels_share_development_mode(env):
    assert Settings(env=env).is_development is True


def test_production_readiness_requires_database_connectivity(monkeypatch):
    import app.db as db

    monkeypatch.setattr(main, "get_settings", lambda: _production_settings())
    monkeypatch.setattr(main, "get_model", lambda: SimpleNamespace(is_available=lambda: True))
    monkeypatch.setattr(db, "get_supabase_client", lambda: None)
    with pytest.raises(HTTPException) as exc:
        main.ready()
    assert exc.value.status_code == 503


def test_production_readiness_checks_live_database(monkeypatch):
    import app.db as db

    class Query:
        def __init__(self, table_name):
            self.table_name = table_name

        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def limit(self, *_args):
            return self

        def execute(self):
            rows = [{"id": "c5b971d1-fe39-40bf-a5cb-539f1a98059f"}] if self.table_name == "hospitals" else []
            return SimpleNamespace(data=rows)

    class Client:
        def table(self, name):
            return Query(name)

    monkeypatch.setattr(main, "get_settings", lambda: _production_settings())
    monkeypatch.setattr(main, "get_model", lambda: SimpleNamespace(is_available=lambda: True))
    monkeypatch.setattr(db, "get_supabase_client", lambda: Client())
    assert main.ready()["database_connected"] is True
