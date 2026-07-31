"""
Tests for config.py warnings (C4) and ruleset_mode alias normalization (D1).
"""
import logging
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from actions.former_companies import compose_company


# Minimal env required by config.load() — satisfies all required fields.
_LOAD_ENV = {
    "SOCRADAR_API_KEY": "test-key",
    "SOCRADAR_COMPANY_ID": "1",
    "ENTRA_TENANT_ID": "tid",
    "ENTRA_CLIENT_ID": "cid",
    "DCR_IMMUTABLE_ID": "dcr-id",
    "DCR_ENDPOINT": "https://dcr.example.com",
    "STORAGE_ACCOUNT_NAME": "sa",
}

# Minimal env required by config.load_former() — satisfies all required fields.
_LOAD_FORMER_ENV = {
    "ENTRA_TENANT_ID": "tid",
    "ENTRA_CLIENT_ID": "cid",
    "SOCRADAR_COMPANY_ID": "1",
    "STORAGE_ACCOUNT_NAME": "sa",
}


def _tenant(active=(), disabled=(), deleted=(), read_ok=True, error=""):
    return {"active": set(active), "disabled": set(disabled),
            "deleted": set(deleted), "read_ok": read_ok, "error": error}


class CredentialIsStoredAsDeliveredTest(unittest.TestCase):
    """Masked or clear is decided per company in the SOCRadar platform. This app
    stores what arrived; a second switch on this side could only disagree with
    the source, and an operator reading Log Analytics would not know which of
    the two to believe."""

    def _load(self, extra=None):
        env = dict(_LOAD_ENV)
        env.update(extra or {})
        with mock.patch.dict(os.environ, env, clear=True):
            from utils import config
            return config.load()

    def test_no_local_password_switch_remains(self):
        conf = self._load()
        self.assertNotIn("enable_log_plaintext_password", conf)

    def test_setting_the_old_variable_changes_nothing(self):
        """An upgraded deployment may still carry the retired app setting."""
        off = self._load({"ENABLE_LOG_PLAINTEXT_PASSWORD": "false"})
        on = self._load({"ENABLE_LOG_PLAINTEXT_PASSWORD": "true"})
        self.assertEqual(off, on)

    def test_a_clear_credential_reaches_the_record(self):
        from utils import sanitize
        fields = sanitize.build_law_password_fields(sanitize.sanitize_password("Yaz**2026!"))
        self.assertEqual(fields["password"], "Yaz**2026!")
        self.assertTrue(fields["is_plaintext"])
        self.assertNotIn("2026", fields["password_masked"])

    def test_a_masked_credential_reaches_the_record_unchanged(self):
        from utils import sanitize
        fields = sanitize.build_law_password_fields(sanitize.sanitize_password("a******3"))
        self.assertEqual(fields["password"], "a******3")
        self.assertFalse(fields["is_plaintext"])

    def test_an_absent_credential_writes_no_password_field(self):
        from utils import sanitize
        fields = sanitize.build_law_password_fields(sanitize.sanitize_password(""))
        self.assertNotIn("password", fields)
        self.assertFalse(fields["password_present"])


class RulesetModeAliasTest(unittest.TestCase):
    """D1: 'standard' (English) must be treated as 'standart' (Turkish)."""

    def test_load_former_standard_becomes_standart(self):
        env = dict(_LOAD_FORMER_ENV, RULESET_MODE="standard")
        with mock.patch.dict(os.environ, env, clear=True):
            from utils import config
            conf = config.load_former()
        self.assertEqual(conf["ruleset_mode"], "standart")

    def test_load_former_standart_stays_standart(self):
        env = dict(_LOAD_FORMER_ENV, RULESET_MODE="standart")
        with mock.patch.dict(os.environ, env, clear=True):
            from utils import config
            conf = config.load_former()
        self.assertEqual(conf["ruleset_mode"], "standart")

    def test_load_former_strict_stays_strict(self):
        env = dict(_LOAD_FORMER_ENV, RULESET_MODE="strict")
        with mock.patch.dict(os.environ, env, clear=True):
            from utils import config
            conf = config.load_former()
        self.assertEqual(conf["ruleset_mode"], "strict")

    def test_load_former_default_is_standart(self):
        env = dict(_LOAD_FORMER_ENV)
        with mock.patch.dict(os.environ, env, clear=True):
            from utils import config
            conf = config.load_former()
        self.assertEqual(conf["ruleset_mode"], "standart")

    def test_compose_company_standard_same_as_standart(self):
        """Both spellings must produce the same desired set in compose_company."""
        row = {"company_id": "330", "own_tenants": ["te330"],
               "api_key": "k", "actor_email": "a@x.com"}
        tenant_data = {
            "te330": _tenant(active={"active@x.com"}, disabled={"disabled@x.com"}),
        }
        kw = dict(include_deleted=True, enable_former_sync=True, enable_cross=False)

        desired_standart, stats_standart, _ = compose_company(
            row, [], tenant_data, ruleset_mode="standart", **kw)
        desired_standard, stats_standard, _ = compose_company(
            row, [], tenant_data, ruleset_mode="standard", **kw)

        self.assertEqual(desired_standart, desired_standard)
        self.assertEqual(stats_standart["desired"], stats_standard["desired"])

    def test_compose_company_strict_differs_from_standart(self):
        """strict routes disabled users to review, not desired -- different from standart."""
        row = {"company_id": "330", "own_tenants": ["te330"],
               "api_key": "k", "actor_email": "a@x.com"}
        tenant_data = {
            "te330": _tenant(active=set(), disabled={"disabled@x.com"}),
        }
        kw = dict(include_deleted=True, enable_former_sync=True, enable_cross=False)

        desired_standart, _, _ = compose_company(
            row, [], tenant_data, ruleset_mode="standart", **kw)
        desired_strict, stats_strict, _ = compose_company(
            row, [], tenant_data, ruleset_mode="strict", **kw)

        self.assertIn("disabled@x.com", desired_standart)
        self.assertNotIn("disabled@x.com", desired_strict)
        self.assertEqual(stats_strict["review_needed"], 1)


if __name__ == "__main__":
    unittest.main()
