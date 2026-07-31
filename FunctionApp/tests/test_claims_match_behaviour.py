"""What the code says about itself has to be true.

Three of the defects in this repository were not wrong logic but wrong claims:
a comment describing the opposite of its own line, a docstring naming a control
that had been removed, and a security promise the module had stopped keeping.
None of them could fail a test, because nothing tested the claim.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_import_path  # noqa: E402,F401  (installs the azure stubs)
from actions import entra_id, law_writer  # noqa: E402
from sources.entra_users import extract_emails  # noqa: E402
from sources.vip import VipFetcher  # noqa: E402
from utils import sanitize  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


class ADeletedUserWhoseNameIsOnlyAnObjectId(unittest.TestCase):
    """The comment says such a UPN is dropped entirely. It has to be."""

    OBJECT_ID = "c5f5dcecc7234174b1d04bcab9f32e77"

    def test_a_prefixed_name_keeps_the_real_address(self):
        got = extract_emails(
            {"userPrincipalName": f"{self.OBJECT_ID}jane.doe@contoso.com"})
        self.assertEqual(got, {"jane.doe@contoso.com"})

    def test_a_name_that_is_nothing_but_the_object_id_is_dropped(self):
        got = extract_emails({"userPrincipalName": f"{self.OBJECT_ID}@contoso.com"})
        self.assertEqual(
            got, set(),
            "an object id was published as somebody's address; nobody owns it, "
            "so reconcile can never retire it either")

    def test_a_real_address_is_untouched(self):
        got = extract_emails({"userPrincipalName": "jane.doe@contoso.com"})
        self.assertEqual(got, {"jane.doe@contoso.com"})


class AVipRecordWhoseFieldsAreFreeText(unittest.TestCase):
    """Containing an '@' is not the same as being an address."""

    def _address(self, rec):
        return VipFetcher._address(rec)

    def test_a_plain_address_is_taken(self):
        self.assertEqual(self._address({"email": "jane@contoso.com"}),
                         "jane@contoso.com")

    def test_a_sentence_that_mentions_an_address_is_not(self):
        self.assertEqual(
            self._address({"keyword": "creds for jane@contoso.com on sale"}), "",
            "free text went to Graph as a person, and the 404 was filed as a "
            "clean miss")

    def test_a_bare_domain_keyword_is_not_an_address(self):
        self.assertEqual(self._address({"keyword": "@contoso.com"}), "")

    def test_a_display_name_is_still_refused(self):
        self.assertEqual(self._address({"vipName": "Jane Doe"}), "")

    def test_the_first_field_that_holds_a_real_address_wins(self):
        self.assertEqual(
            self._address({"vipName": "Jane Doe", "email": "jane@contoso.com",
                           "keyword": "other@contoso.com"}),
            "jane@contoso.com")


class TheCredentialClaimsInTheSource(unittest.TestCase):
    """These modules once promised the plaintext is never stored. It is."""

    def test_the_writer_does_not_name_a_control_that_does_not_exist(self):
        doc = law_writer.__doc__ or ""
        self.assertNotIn(
            "EnableLogPlaintextPassword", doc,
            "the module documents a switch that is nowhere in the code or the "
            "template, so a reader believes the credential is protected")

    def test_the_setting_really_is_absent_everywhere(self):
        hits = []
        sources = [p for p in REPO.glob("FunctionApp/**/*.py") if "tests" not in p.parts]
        for path in sources + list(REPO.glob("deploy/*.bicep")):
            if "EnableLogPlaintextPassword" in path.read_text(encoding="utf-8"):
                hits.append(str(path.relative_to(REPO)))
        self.assertEqual(hits, [], "the claim would be true — restore the doc instead")

    def test_the_sanitiser_does_not_claim_the_plaintext_is_dropped(self):
        doc = sanitize.__doc__ or ""
        self.assertNotRegex(
            doc, r"plaintext is NEVER stored",
            "build_law_password_fields writes the raw value and the table "
            "declares a column for it")

    def test_and_the_behaviour_it_now_describes_is_the_real_one(self):
        fields = sanitize.build_law_password_fields(
            sanitize.sanitize_password("Sup3rSecret!"))
        self.assertEqual(fields["password"], "Sup3rSecret!")
        self.assertEqual(fields["password_masked"], "S***!")


class ARetryThatCouldRepeatAnIrreversibleChange(unittest.TestCase):
    """503 does not promise the change was not made. 429 does."""

    class _Resp:
        def __init__(self, status):
            self.status_code = status
            self.headers = {"Retry-After": "1"}

    def _attempts(self, method, status):
        calls = {"n": 0}

        def request(m, url, **kw):
            calls["n"] += 1
            return self._Resp(status)

        import time as _time
        real_request, real_sleep = entra_id.requests.request, _time.sleep
        entra_id.requests.request = request
        _time.sleep = lambda *_a: None
        try:
            entra_id._graph_request(method, "https://graph.test/v1.0/users/x", {},
                                    max_retries=2)
        finally:
            entra_id.requests.request = real_request
            _time.sleep = real_sleep
        return calls["n"]

    def test_throttling_is_retried_for_a_mutation(self):
        self.assertEqual(self._attempts("DELETE", 429), 3)

    def test_a_gateway_failure_is_not_retried_for_a_mutation(self):
        self.assertEqual(
            self._attempts("DELETE", 503), 1,
            "the delete may already have happened; the second attempt answers "
            "404 and the audit trail records 'no action taken'")

    def test_a_gateway_failure_is_still_retried_for_a_read(self):
        self.assertEqual(self._attempts("GET", 504), 3)


class TheLookupAsksForWhatTheAuditReports(unittest.TestCase):
    """Graph omits accountEnabled from its default projection."""

    def test_the_select_names_it(self):
        self.assertIn("accountEnabled", entra_id.LOOKUP_SELECT)


class TheImportAuditSchemaMatchesWhatIsWritten(unittest.TestCase):
    """A stream that does not declare a column drops it without saying so."""

    def _declared_columns(self):
        template = (REPO / "deploy" / "azuredeploy.json").read_text(encoding="utf-8")
        import json
        doc = json.loads(template)
        columns = doc["variables"]["importAuditColumns"]
        return {c["name"] for c in columns}

    def test_every_counter_written_has_a_column(self):
        source = (REPO / "FunctionApp" / "actions" / "law_writer.py").read_text(encoding="utf-8")
        block = source.split("def write_audit")[1]
        written = set(re.findall(r'"([a-z_]+)":\s+r\.get\(', block))
        written |= set(re.findall(r'"([a-z_]+)":\s+(?:bool|float|str)\(r\.get\(', block))
        missing = sorted(written - self._declared_columns())
        self.assertEqual(
            missing, [],
            f"these fields are uploaded but not declared, so ingestion drops "
            f"them silently: {missing}")

    def test_the_new_counters_are_among_them(self):
        declared = self._declared_columns()
        for column in ("no_address_count", "lookup_disabled_count", "no_token_count"):
            self.assertIn(column, declared)


if __name__ == "__main__":
    unittest.main()
