"""Findings of the independent audit (task_azure_0080) that were reproducible.

Each class fails on the code before the fix and passes after it.
"""

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_import_path import _ensure_azure_stub  # noqa: E402

_ensure_azure_stub()

import function_app  # noqa: E402
from test_action_gates import _FakeLedger, _conf, _run  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def _prose(path) -> str:
    return " ".join(Path(path).read_text(encoding="utf-8").replace("*", "").split())


def _one_person():
    return [{"email": "a@x.com", "_checkpoint_update": {"last_start_date": "2026-07-27"}}]


class AFailedActionIsCountedInTheRunSummary(unittest.TestCase):
    """The failure used to live only in the row's actions_taken, so a run that
    could not disable a single account reported error_count 0."""

    def test_one_failed_action_is_one_error(self):
        result, records, _ = _run(_conf(enable_disable_account=True), _one_person(),
                                  _FakeLedger(),
                                  disable_account=mock.Mock(return_value=False))
        self.assertIn("disable_account_failed", records[0]["actions_taken"])
        self.assertEqual(result["errors"], 1)

    def test_every_failed_action_counts(self):
        result, _, _ = _run(_conf(enable_disable_account=True, enable_revoke_session=True),
                            _one_person(), _FakeLedger(),
                            disable_account=mock.Mock(return_value=False),
                            revoke_sessions=mock.Mock(return_value=False))
        self.assertEqual(result["errors"], 2)

    def test_a_clean_run_stays_at_zero(self):
        result, _, _ = _run(_conf(enable_disable_account=True), _one_person(),
                            _FakeLedger(),
                            disable_account=mock.Mock(return_value=True))
        self.assertEqual(result["errors"], 0)

    def test_plan_mode_is_not_an_error(self):
        result, records, _ = _run(_conf(enable_disable_account=True,
                                        entra_action_mode="plan"),
                                  _one_person(), _FakeLedger())
        self.assertIn("disable_account_planned", records[0]["actions_taken"])
        self.assertEqual(result["errors"], 0)

    def test_a_missing_mfa_permission_is_visible_too(self):
        denied = {"methods_deleted": 0, "methods_skipped": 0, "errors": ["x"],
                  "permission_denied": True}
        result, records, _ = _run(_conf(enable_force_mfa_reregistration=True),
                                  _one_person(), _FakeLedger(),
                                  force_mfa_reregistration=mock.Mock(return_value=denied))
        self.assertIn("force_mfa_rereg_no_permission", records[0]["actions_taken"])
        self.assertEqual(result["errors"], 1)


class APartialMfaResetIsNotAFinishedOne(unittest.TestCase):
    """One method deleted and one left behind used to be filed as success and
    written to the ledger, so the leftover was never retried."""

    PARTIAL = {"methods_deleted": 1, "methods_skipped": 1,
               "errors": ["unknown method type: hardwareOath"],
               "permission_denied": False}

    def test_it_is_reported_as_partial(self):
        _, records, _ = _run(_conf(enable_force_mfa_reregistration=True), _one_person(),
                             _FakeLedger(),
                             force_mfa_reregistration=mock.Mock(return_value=self.PARTIAL))
        taken = records[0]["actions_taken"]
        self.assertIn("force_mfa_rereg_partial", taken)
        self.assertNotIn("force_mfa_rereg", taken)

    def test_it_is_not_recorded_as_done(self):
        ledger = _FakeLedger()
        _run(_conf(enable_force_mfa_reregistration=True), _one_person(), ledger,
             force_mfa_reregistration=mock.Mock(return_value=self.PARTIAL))
        self.assertEqual(ledger.recorded, [])

    def test_it_counts_as_an_error(self):
        result, _, _ = _run(_conf(enable_force_mfa_reregistration=True), _one_person(),
                            _FakeLedger(),
                            force_mfa_reregistration=mock.Mock(return_value=self.PARTIAL))
        self.assertEqual(result["errors"], 1)

    def test_a_complete_reset_is_still_recorded(self):
        ok = {"methods_deleted": 2, "methods_skipped": 1, "errors": [],
              "permission_denied": False}
        ledger = _FakeLedger()
        result, records, _ = _run(_conf(enable_force_mfa_reregistration=True),
                                  _one_person(), ledger,
                                  force_mfa_reregistration=mock.Mock(return_value=ok))
        self.assertIn("force_mfa_rereg", records[0]["actions_taken"])
        self.assertEqual(ledger.recorded, [("a@x.com", "force_mfa_rereg")])
        self.assertEqual(result["errors"], 0)


class ABodyThatIsNotAnObjectIsABadRequest(unittest.TestCase):
    """req.get_json() returns whatever was sent; a list has no .get()."""

    @staticmethod
    def _req(body):
        req = mock.Mock()
        req.get_json.return_value = body
        return req

    def test_probe_refuses_a_list(self):
        resp = function_app.leak_probe(self._req([]))
        self.assertEqual(resp.status_code, 400)

    def test_probe_refuses_a_string(self):
        resp = function_app.leak_probe(self._req("x"))
        self.assertEqual(resp.status_code, 400)

    def test_manual_refuses_a_list(self):
        resp = function_app.former_manual(self._req([1, 2]))
        self.assertEqual(resp.status_code, 400)


class FormerLifecycleEventsNameTheirCompany(unittest.TestCase):
    """The events were written with the template placeholder as company_id,
    so a per-company query never saw them."""

    ROW = {"company_id": "777", "api_key": "k", "actor_email": "a@x.com",
           "own_tenants": ["t1"]}

    def test_company_failed_carries_the_company(self):
        fconf = {"enable_former_sync": True, "enable_cross_tenant_suppress": True,
                 "socradar_company_id": "unused-former-map-mode", "group_tenant_ids": [],
                 "former_client_mode": "real"}
        run = {"topology": "map", "rows": [self.ROW], "row_errors": [], "guard_notes": [],
               "tripped": [], "snaps": [{"row": self.ROW, "error": "boom"}]}
        timer = mock.Mock(past_due=False)
        with mock.patch.object(function_app.cfg, "load_former", return_value=fconf), \
             mock.patch.object(function_app, "DefaultAzureCredential", lambda *a, **k: None), \
             mock.patch.object(function_app, "_former_company_snapshots", return_value=run), \
             mock.patch.object(function_app.law, "write_former_audit"), \
             mock.patch.object(function_app.law, "write_lifecycle_event") as event:
            function_app.former_employee_sync(timer)
        self.assertTrue(event.called, "the failure was not recorded at all")
        written_for = event.call_args.args[0]["socradar_company_id"]
        self.assertEqual(written_for, "777")

    def _run_apply(self, *, execute, lease_held=True):
        """Drive one applied company through the timer and return the events."""
        fconf = {"enable_former_sync": True, "enable_cross_tenant_suppress": True,
                 "socradar_company_id": "unused-former-map-mode", "group_tenant_ids": [],
                 "former_client_mode": "real", "former_apply_changes": True,
                 "storage_account_name": "sa"}
        plan = mock.Mock(notes=[], blocked=False, add=[], remove=[], preserve=[],
                         withheld_removals=[], block_reason="")
        stats = dict.fromkeys(("own_active", "own_former", "sibling_active",
                               "review_needed", "manual", "desired"), 0)
        snap = {"row": self.ROW, "error": None, "plan": plan, "stats": stats,
                "client": object(), "ownership": object(), "current": [], "owned": []}
        run = {"topology": "map", "rows": [self.ROW], "row_errors": [], "guard_notes": [],
               "tripped": [], "snaps": [snap]}
        lock = mock.Mock()
        lock.acquire.return_value = True
        lock.still_held.return_value = lease_held
        with mock.patch.object(function_app.cfg, "load_former", return_value=fconf), \
             mock.patch.object(function_app, "DefaultAzureCredential", lambda *a, **k: None), \
             mock.patch.object(function_app, "_former_company_snapshots", return_value=run), \
             mock.patch.object(function_app.companies_api, "company_effective_apply",
                               return_value=(True, "")), \
             mock.patch.object(function_app.lock_api, "TableLeaseLock", return_value=lock), \
             mock.patch.object(function_app.planner_api, "execute_former_plan", execute), \
             mock.patch.object(function_app.law, "write_former_audit"), \
             mock.patch.object(function_app.law, "write_lifecycle_event") as event:
            function_app.former_employee_sync(mock.Mock(past_due=False))
        return {c.args[1]: c.args[0]["socradar_company_id"] for c in event.call_args_list}

    def test_apply_failed_carries_the_company(self):
        written = self._run_apply(execute=mock.Mock(side_effect=RuntimeError("boom")))
        self.assertEqual(written.get("former_apply_failed"), "777")

    def test_lease_lapsed_carries_the_company(self):
        ok = {"added": 0, "removed": 0, "add_planned": 0, "remove_planned": 0}
        written = self._run_apply(execute=mock.Mock(return_value=ok), lease_held=False)
        self.assertEqual(written.get("former_lease_lapsed_during_apply"), "777")

    def test_mutation_unconfirmed_carries_the_company(self):
        res = {"added": 0, "removed": 0, "add_planned": 1, "remove_planned": 0,
               "unconfirmed_adds": 1, "unconfirmed_reason": "readback"}
        written = self._run_apply(execute=mock.Mock(return_value=res))
        self.assertEqual(written.get("former_mutation_unconfirmed"), "777")


class ActionSwitchesFailClosed(unittest.TestCase):
    """The template writes every switch, so the code default only matters for an
    install without it. It must not arm a directory change."""

    def test_group_and_revoke_default_off(self):
        env = {"SOCRADAR_API_KEY": "k", "SOCRADAR_COMPANY_ID": "1",
               "ENTRA_TENANT_ID": "tid", "ENTRA_CLIENT_ID": "cid",
               "DCR_IMMUTABLE_ID": "d", "DCR_ENDPOINT": "https://dcr.example.com",
               "STORAGE_ACCOUNT_NAME": "sa"}
        with mock.patch.dict(os.environ, env, clear=True):
            from utils import config
            conf = config.load()
        self.assertFalse(conf["enable_add_to_group"])
        self.assertFalse(conf["enable_revoke_session"])


class PolicyAndGateDescribeTheStagedPackage(unittest.TestCase):
    """Since 7 Sep an installation holds a blob pointer, not the release URL."""

    def test_policy_no_longer_says_running_installs_follow_the_asset(self):
        text = _prose(REPO / "docs" / "product-policy.md")
        self.assertNotIn("moves every running installation", text)
        self.assertIn("does not reach", text)

    def test_the_gate_comment_does_not_claim_a_live_url(self):
        text = _prose(REPO / "scripts" / "release_gate.sh")
        self.assertNotIn("picks up untested code", text)

    def test_the_gate_demands_the_subscription_from_the_environment(self):
        """No subscription GUID in a public repo; an unset variable must stop the gate."""
        text = (REPO / "scripts" / "release_gate.sh").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
        self.assertIn("GATE_EXPECTED_SUBSCRIPTION_ID", text)

    def test_the_gate_fails_when_the_subscription_is_not_given(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            az = Path(d) / "az"
            az.write_text("#!/bin/sh\necho x\n")
            az.chmod(0o755)
            env = {k: v for k, v in os.environ.items() if k != "GATE_EXPECTED_SUBSCRIPTION_ID"}
            env.update(PATH=f"{d}:{env['PATH']}", GATE_COMPANY_ID="1",
                       GATE_API_KEY="k", GATE_APP_ID="a")
            r = subprocess.run(["bash", str(REPO / "scripts" / "release_gate.sh")],
                               capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("GATE_EXPECTED_SUBSCRIPTION_ID", r.stdout + r.stderr)

    def test_the_gate_still_parses(self):
        r = subprocess.run(["bash", "-n", str(REPO / "scripts" / "release_gate.sh")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class TheReadmeAndSourcesSayWhatTheCodeDoes(unittest.TestCase):

    README = _prose(REPO / "README.md")

    def test_the_failure_query_sees_a_failed_action(self):
        self.assertIn('actions_taken) contains "_failed"', self.README)

    def test_a_failed_action_is_not_promised_a_retry_but_is_counted(self):
        self.assertIn("counted in error_count", self.README)

    def test_run_on_startup_is_described_as_every_host_start(self):
        bicep = (REPO / "deploy" / "main.bicep").read_text(encoding="utf-8")
        desc = re.search(r"@description\('([^']*)'\)\s*param RunOnStartup", bicep).group(1)
        self.assertIn("every", desc)
        self.assertIn("every host start", self.README)

    def test_upn_only_matching_is_stated(self):
        self.assertIn("user principal name", self.README)

    def test_botnet_docstring_no_longer_claims_the_password_is_never_stored(self):
        src = (REPO / "FunctionApp" / "sources" / "botnet.py").read_text(encoding="utf-8")
        self.assertNotIn("plaintext password never stored", src)

    def test_the_stale_production_blocked_comment_is_gone(self):
        src = (REPO / "FunctionApp" / "utils" / "config.py").read_text(encoding="utf-8")
        self.assertNotIn("production-blocked", src)


class TheTemplateBoundsItsCaps(unittest.TestCase):
    """azuredeploy.json is what the Deploy button serves, so check the built file."""

    PARAMS = json.loads((REPO / "deploy" / "azuredeploy.json").read_text())["parameters"]

    def test_removal_percent_cannot_exceed_100(self):
        p = self.PARAMS["FormerMaxRemovalPercent"]
        self.assertEqual(p.get("maxValue"), 100)
        self.assertEqual(p.get("minValue"), 0)

    def test_add_and_removal_caps_cannot_be_negative(self):
        for name in ("FormerMaxAdds", "FormerMaxRemovals"):
            self.assertEqual(self.PARAMS[name].get("minValue"), 0, name)
            # arm-ttk "Min And Max Value Are Numbers" wants both or neither.
            self.assertIn("maxValue", self.PARAMS[name], name)

    def test_retention_has_an_upper_bound(self):
        self.assertIn("maxValue", self.PARAMS["LeakLedgerRetentionDays"])


def _leak_run(values, tenants=None, conf_over=None):
    """Run one botnet-shaped source over `values`; return (summary, graph_calls, rows, saved)."""
    from actions import law_writer
    conf = dict(_conf(), enable_user_lookup=True, verified_domains=[],
                entra_action_mode="plan",
                storage_account_name="sa", tenant_ids=["t-a"], tenant_id="",
                client_id="app-1", socradar_company_id="1234567",
                dcr_immutable_id="dcr-1", dcr_endpoint="https://e.invalid",
                initial_lookback_minutes=43200, initial_start_date="")
    conf.update(conf_over or {})
    emps = [{"email": v, "url": f"https://x.invalid/{i}", "source": "botnet",
             "is_employee": True} for i, v in enumerate(values)]
    emps[-1]["_checkpoint_update"] = {"last_start_date": "2026-07-27"}
    tenants = {"t-a": {"Authorization": "x"}} if tenants is None else tenants
    with mock.patch.object(function_app.src_botnet, "fetch", return_value=emps), \
         mock.patch.object(function_app.cp, "load", return_value={}), \
         mock.patch.object(function_app.cp, "save") as save, \
         mock.patch.object(function_app.entra, "lookup_user",
                           return_value=(None, 404)) as lu, \
         mock.patch.object(law_writer, "_upload", return_value=True) as up:
        out = function_app._process_source(
            "botnet", conf, None, tenants, deadline=float("inf"),
            checkpoint_key="botnet:1234567")
    rows = [r for c in up.call_args_list
            if c[0][1] == law_writer.STREAM_MAP["botnet"] for r in c[0][2]]
    return out, lu, rows, save


class ANonAddressIsNotSentToGraph(unittest.TestCase):
    """Policy 13: where there is no address, Graph is not asked.

    A botnet `user` is often a number or a login name. Looked up as a UPN it
    comes back 404 and was counted as "address not in the directory".
    """

    NOT_ADDRESSES = ["1234567890123", "jsmith", "Jane Doe", "a@", "@b.com", "a@b@c.com"]

    def test_graph_is_not_asked(self):
        _, lu, _, _ = _leak_run(self.NOT_ADDRESSES)
        lu.assert_not_called()

    def test_each_one_lands_in_the_no_address_bucket(self):
        out, _, rows, _ = _leak_run(self.NOT_ADDRESSES)
        self.assertEqual(out["no_address"], len(self.NOT_ADDRESSES))
        self.assertEqual(out["not_found"], 0)
        self.assertEqual({r["entra_status"] for r in rows}, {"skipped_no_address"})
        self.assertEqual(len(rows), len(self.NOT_ADDRESSES))

    def test_an_address_is_still_looked_up(self):
        out, lu, _, _ = _leak_run(["1234567890123", "someone@x.invalid"])
        self.assertEqual(lu.call_count, 1)
        self.assertEqual((out["no_address"], out["not_found"]), (1, 1))

    def test_the_buckets_still_close(self):
        out, _, _, _ = _leak_run(["1234567890123", "someone@x.invalid", "jsmith"])
        parts = sum(out[k] for k in ("found", "not_found", "no_address", "domain_filtered"))
        self.assertEqual(parts, out["total"])

    def test_it_does_not_hold_the_window(self):
        _, _, _, save = _leak_run(self.NOT_ADDRESSES)
        saved = save.call_args[0][3]
        self.assertEqual(saved.get("last_start_date"), "2026-07-27")
        self.assertEqual(saved.get("consecutive_holds"), 0)


class ARereadWindowWritesItsRowsAgain(unittest.TestCase):
    """README: rows are appended and never deduplicated; a held window repeats."""

    def test_a_held_window_is_written_twice(self):
        # No tenant produced a token: rows are written as skipped_no_token and the
        # window is held. The next run reads the same records and writes them again.
        first = _leak_run(["a@x.invalid", "b@x.invalid"], tenants={})
        second = _leak_run(["a@x.invalid", "b@x.invalid"])
        self.assertEqual({r["entra_status"] for r in first[2]}, {"skipped_no_token"})
        self.assertEqual({r["entra_status"] for r in second[2]}, {"not_found"})
        held = first[3].call_args[0][3]
        self.assertEqual(held.get("consecutive_holds"), 1)
        self.assertNotIn("last_start_date", held)
        # same finding, two rows, different entra_status
        self.assertEqual([r["email"] for r in first[2]], [r["email"] for r in second[2]])

    def test_the_readme_says_so(self):
        text = _prose(REPO / "README.md")
        self.assertNotIn("one row per finding", text)
        self.assertIn("appended and never deduplicated", text)
        self.assertIn("arg_max(TimeGenerated", text)

    def test_the_readme_says_a_non_address_is_not_sent(self):
        text = _prose(REPO / "README.md")
        self.assertIn("does not contain exactly one @", text)


if __name__ == "__main__":
    unittest.main()
