"""The package cannot arrive through a redirecting URL any more.

Azure rejects the CREATE of a Linux consumption Function App whose
WEBSITE_RUN_FROM_PACKAGE points at a URL that redirects. A GitHub release
download URL always redirects (302 to objects.githubusercontent.com), so the
one-click deploy fails at the Function App with BadRequest 51024 and leaves the
storage account, the identity and the App Service Plan behind.

Measured 7 Sep 2026 on an isolated rig: the same release URL was accepted at
15:01 and rejected at 15:10, same subscription, same region. Nothing in this
repository changed in between, and a green deployment from the day before proved
nothing about the day after.

The app is now created with WEBSITE_RUN_FROM_PACKAGE='1' and the zip is pushed
by the packagePush deploymentScript, which is the platform's own suggested shape.

Two things here are easy to lose and invisible when lost:

  * forceUpdateTag. Without it a redeploy PUTs the site with the full appSettings
    list, resetting the setting from the blob URL config-zip wrote back to '1',
    while the script -- unchanged -- does not re-run. Succeeded, and no code.

  * the RunOnStartup condition. The push used to be gated on RunOnStartup as
    well, which was harmless when the app fetched its own package from the URL.
    With '1' the app starts empty, so that gate would leave RunOnStartup=false
    deployments with zero functions.

It reads files and nothing else: no app import, no Azure stub, no network.
"""

import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "deploy" / "azuredeploy.json"
BICEP = REPO / "deploy" / "main.bicep"


def _template():
    return json.loads(TEMPLATE.read_text())


def _resources(doc):
    """languageVersion 2.0 keys resources by symbolic name rather than listing them."""
    res = doc.get("resources")
    return list(res.values()) if isinstance(res, dict) else list(res or [])


class PackagePush(unittest.TestCase):
    def setUp(self):
        self.doc = _template()
        self.resources = _resources(self.doc)
        self.params = self.doc.get("parameters", {})
        # Calibration: every assertion below is vacuous if the shape is not what
        # this test assumes, and an empty search reads exactly like a pass.
        self.assertTrue(
            any(r.get("type") == "Microsoft.Web/sites" for r in self.resources),
            "no Microsoft.Web/sites found in azuredeploy.json - these checks would "
            "all pass without testing anything",
        )

    def _sites(self):
        return [r for r in self.resources if r.get("type") == "Microsoft.Web/sites"]

    def _scripts(self):
        return [r for r in self.resources
                if r.get("type") == "Microsoft.Resources/deploymentScripts"
                and "package" in json.dumps(r.get("properties", {})).lower()]

    def test_run_from_package_is_one_not_a_url(self):
        for site in self._sites():
            settings = {a.get("name"): a.get("value") for a in
                        ((site.get("properties") or {}).get("siteConfig") or {})
                        .get("appSettings", [])}
            value = settings.get("WEBSITE_RUN_FROM_PACKAGE")
            self.assertEqual(
                value, "1",
                "WEBSITE_RUN_FROM_PACKAGE is %r. Azure rejects the create when it "
                "is a URL that redirects, and the release URL always redirects." % (value,),
            )
            self.assertNotIn(
                "PackageUri", str(value),
                "WEBSITE_RUN_FROM_PACKAGE is back to the release URL - the exact "
                "shape the platform rejects with BadRequest 51024",
            )

    def test_the_package_is_pushed(self):
        scripts = self._scripts()
        self.assertEqual(len(scripts), 1,
                         "expected exactly one package-push deploymentScript, found %d"
                         % len(scripts))
        body = (scripts[0].get("properties") or {}).get("scriptContent") or ""
        self.assertIn("az storage blob upload", body,
                      "the script does not push the package - with '1' and no push the "
                      "app deploys green and empty")
        self.assertIn("PACKAGE_URL", body, "the script does not download PackageUri")
        # A 404 or an HTML error page is still a file; pushing one indexes nothing.
        self.assertIn("zipfile.ZipFile", body,
                      "the downloaded package is not verified as a readable zip")
        # Running is not health. The count is the only signal that the code loaded.
        self.assertIn("length(value)", body,
                      "the script does not read the function count back")
        self.assertIn("exit 1", body,
                      "the script does not fail when no function was indexed")

    def test_the_verdict_is_two_readings_not_the_exit_code(self):
        """ARM reported 1 function on an app whose host answered 503 with the
        pointer left at "1": the count alone is not proof the package is stored
        where the app reloads it. Both readings, and the pointer never echoed."""
        body = (self._scripts()[0].get("properties") or {}).get("scriptContent") or ""
        self.assertIn("the readings decide", body,
                      "the script trusts config-zip's exit code; it reported a "
                      "failure after the package had landed")
        self.assertIn("staged()", body,
                      "nothing checks that the pointer became a blob under "
                      "function-releases - a package staged elsewhere is lost "
                      "on the next restart")
        self.assertIn("function-releases", body,
                      "the pointer reading does not look for the blob container")
        self.assertEqual(body.count("$(pointer)"), 1,
                         "the pointer value is read more than once; it carries a "
                         "SAS token and belongs only in `case`")
        self.assertIn('case "$(pointer)" in', body,
                      "the pointer value is not read into `case`")
        self.assertIn("for attempt in", body,
                      "a single push is a coin flip against an app that is still "
                      "provisioning")
        self.assertIn("for wait in $(seq 1 8)", body,
                      "the settings read is not retried; the wait belongs on the "
                      "first call that needs the Website Contributor assignment")
        # Measured on a TAXII run: the pointer was written, no restart followed
        # and the function never appeared -- it indexed one minute after an
        # explicit restart. Writing the pointer does not reload the host.
        self.assertIn("functionapp restart", body,
                      "the script writes the package pointer and only waits")

    def test_every_variable_the_script_reads_is_declared(self):
        """An undeclared variable is the empty string in bash, and `curl -L ""`
        returns HTTP 000 - a failure that blames the package URL, not the template."""
        script = self._scripts()[0]
        props = script.get("properties") or {}
        declared = {e.get("name") for e in (props.get("environmentVariables") or [])}
        used = set(re.findall(r"\$([A-Z][A-Z0-9_]*)", props.get("scriptContent") or ""))
        # Names the script assigns itself (BLOB, CONN, SA, SAS) are not the
        # template's job to declare.
        used -= set(re.findall(r"(?:^|[\s;&|{(])([A-Z][A-Z0-9_]*)=", props.get("scriptContent") or ""))
        self.assertFalse(sorted(used - declared),
                         "the script reads %s but the template does not declare them"
                         % sorted(used - declared))

    def test_a_redeploy_pushes_again(self):
        props = self._scripts()[0].get("properties") or {}
        tag = props.get("forceUpdateTag")
        self.assertTrue(tag,
                        "no forceUpdateTag: a redeploy resets WEBSITE_RUN_FROM_PACKAGE "
                        "from the blob URL back to '1' and the push does not re-run - "
                        "the deployment reports Succeeded with no code on the app")
        ref = re.match(r"^\[parameters\('([^']+)'\)\]$", str(tag))
        self.assertIsNotNone(ref,
                             "forceUpdateTag is %r - it has to reference a parameter "
                             "whose default changes per deployment" % (tag,))
        self.assertEqual(
            self.params.get(ref.group(1), {}).get("defaultValue"), "[utcNow()]",
            "%s does not default to utcNow(), so the tag never changes and the push "
            "is skipped on every redeploy" % ref.group(1),
        )

    def test_the_push_is_not_gated_on_run_on_startup(self):
        cond = str(self._scripts()[0].get("condition") or "")
        self.assertNotIn(
            "RunOnStartup", cond,
            "the package push is gated on RunOnStartup (%s). That was safe while the "
            "app fetched its own package from the URL; with '1' it leaves "
            "RunOnStartup=false deployments with zero functions." % cond,
        )

    def test_the_pusher_can_reach_the_app(self):
        """The script reads and writes app settings through ARM, so the Website
        Contributor assignment has to exist and carry the same condition."""
        deps = json.dumps(self._scripts()[0].get("dependsOn") or [])
        self.assertIn("faContributorRole", deps,
                      "the push does not wait for the Website Contributor assignment")
        roles = [r for r in self.resources
                 if r.get("type") == "Microsoft.Authorization/roleAssignments"
                 and "WebsiteContributor" in json.dumps(r.get("name", ""))]
        self.assertTrue(roles, "the Website Contributor assignment is gone")
        for role in roles:
            self.assertNotIn(
                "RunOnStartup", str(role.get("condition") or ""),
                "the role is gated on RunOnStartup but the push is not, so the push "
                "would run without the rights it needs",
            )

    def test_the_source_and_the_built_template_agree(self):
        """The deploy button serves azuredeploy.json; anyone editing edits main.bicep.
        CI checks the whole file, but a stale build of exactly this part is the one
        that ships a customer a template nobody reviewed."""
        src = BICEP.read_text()
        self.assertIn("forceUpdateTag: _packagePushTimestamp", src,
                      "main.bicep has no forceUpdateTag on the push")
        self.assertIn("az storage blob upload", src, "main.bicep does not stage the package")
        self.assertIn("value: '1' }", src.replace("value: '1'}", "value: '1' }"),
                      "main.bicep does not set WEBSITE_RUN_FROM_PACKAGE to '1'")


if __name__ == "__main__":
    unittest.main()
