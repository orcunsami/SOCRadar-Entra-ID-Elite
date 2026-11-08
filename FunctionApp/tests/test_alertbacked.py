"""The deployed rule must preserve opt-in, tenant separation and safe output."""
import itertools
import json
from pathlib import Path
import re
import unittest

REPO = Path(__file__).resolve().parents[2]

class AlertBackedContract(unittest.TestCase):
    def setUp(self):
        self.template = json.loads((REPO / 'deploy/azuredeploy.json').read_text())
        self.resources = self.template['resources']
        self.module = self.resources['alertRules']
        self.rule = self.module['properties']['template']['resources'][0]
        self.props = self.rule['properties']

    def test_default_and_legacy_mode_precedence(self):
        p = self.template['parameters']
        self.assertEqual(p['IncidentMode']['defaultValue'], 'None')
        self.assertEqual(p['IncidentMode']['allowedValues'], ['None', 'AlertBacked', 'Direct'])
        self.assertIs(p['EnableCreateIncident']['defaultValue'], False)
        self.assertEqual(self.template['variables']['effectiveIncidentMode'],
                         "[if(and(equals(parameters('IncidentMode'), 'None'), parameters('EnableCreateIncident')), 'Direct', parameters('IncidentMode'))]")
        # All six public combinations have an explicit, mutually exclusive result.
        for mode, legacy in itertools.product(p['IncidentMode']['allowedValues'], [False, True]):
            effective = 'Direct' if mode == 'None' and legacy else mode
            self.assertEqual(effective == 'AlertBacked', mode == 'AlertBacked')
            self.assertFalse(effective == 'AlertBacked' and effective == 'Direct')

    def test_rule_is_stable_and_only_alertbacked_enables_it(self):
        condition = "[equals(variables('effectiveIncidentMode'), 'AlertBacked')]"
        self.assertEqual(self.module['condition'], condition)
        self.assertEqual(self.resources['sentinelOnboarding']['condition'], condition)
        self.assertEqual(self.module['properties']['parameters']['Enabled']['value'],
                         "[equals(variables('effectiveIncidentMode'), 'AlertBacked')]")
        self.assertEqual(self.props['enabled'], "[parameters('Enabled')]")
        self.assertEqual(self.rule['kind'], 'Scheduled')
        for dependency in ['sentinelOnboarding', 'botnetTable', 'piiTable', 'vipTable']:
            self.assertIn(dependency, self.module['dependsOn'])
        self.assertIn('elite-leaked-credential-rule', self.rule['name'])
        self.assertEqual(self.resources['sentinelOnboarding']['properties'], {})

    def test_direct_has_workspace_identifiers_and_least_privilege(self):
        settings = {s['name']: s['value'] for s in self.resources['functionApp']['properties']['siteConfig']['appSettings']}
        self.assertEqual(settings['ENABLE_CREATE_INCIDENT'], "[string(equals(variables('effectiveIncidentMode'), 'Direct'))]")
        self.assertEqual(settings['WORKSPACE_NAME'], "[variables('workspaceName')]")
        self.assertEqual(settings['WORKSPACE_RESOURCE_GROUP'], "[resourceGroup().name]")
        self.assertEqual(settings['SUBSCRIPTION_ID'], "[subscription().subscriptionId]")
        role = self.resources['sentinelResponderRole']
        self.assertEqual(role['condition'], "[equals(variables('effectiveIncidentMode'), 'Direct')]")
        self.assertIn('3e150937-b8fe-4cfb-8069-0eaf05ecd056', role['properties']['roleDefinitionId'])
        self.assertIn('workspace', role['scope'].lower())
        self.assertIn('sentinelResponderRole', self.resources['functionApp']['dependsOn'])
        self.assertNotIn('sentinelOnboarding', self.resources['functionApp']['dependsOn'])
        self.assertEqual(self.resources['workspace']['properties']['sku']['name'], 'PerGB2018')

    def test_none_and_direct_skip_sentinel_resources(self):
        for mode, legacy in itertools.product(['None', 'Direct', 'AlertBacked'], [False, True]):
            effective = 'Direct' if mode == 'None' and legacy else mode
            for name in ['sentinelOnboarding', 'alertRules']:
                condition = self.resources[name].get('condition', '')
                match = re.fullmatch(r"\[equals\(variables\('effectiveIncidentMode'\), '([^']+)'\)\]", condition)
                self.assertIsNotNone(match, name)
                self.assertEqual(effective == match.group(1), mode == 'AlertBacked')

    def test_schedule_and_incident_contract(self):
        self.assertEqual(self.props['queryFrequency'], 'PT1H')
        self.assertEqual(self.props['queryPeriod'], 'P7D')
        self.assertIs(self.props['suppressionEnabled'], False)
        self.assertEqual(self.props['eventGroupingSettings']['aggregationKind'], 'AlertPerResult')
        self.assertIs(self.props['incidentConfiguration']['createIncident'], True)
        self.assertIs(self.props['incidentConfiguration']['groupingConfiguration']['enabled'], False)
        mapping = self.props['entityMappings'][0]
        self.assertEqual(mapping, {'entityType': 'Account', 'fieldMappings': [
            {'identifier': 'Name', 'columnName': 'AccountName'},
            {'identifier': 'UPNSuffix', 'columnName': 'UPNSuffix'}]})
        for field in ['alertDisplayNameFormat', 'alertDescriptionFormat']:
            self.assertLessEqual(len(re.findall(r'\{\{[^}]+\}\}', self.props['alertDetailsOverride'][field])), 3)
        self.assertEqual(self.props['alertDetailsOverride']['alertSeverityColumnName'], 'SentinelSeverity')
        self.assertEqual(self.props['tactics'], ['InitialAccess'])

    def test_query_covers_only_finding_tables_and_projects_no_credentials(self):
        q = self.props['query']
        self.assertEqual(set(re.findall(r'SOCRadar_\w+_CL', q)),
                         {'SOCRadar_Botnet_CL', 'SOCRadar_PII_CL', 'SOCRadar_VIP_CL'})
        self.assertNotRegex(q, r'(?i)password|credential|project\s+\*')
        self.assertIn('is_employee == true', q)
        self.assertIn('isnotempty(company_id)', q)
        self.assertIn('isnotempty(email)', q)
        self.assertIn("tolower(entra_status) in ('found', 'compromised')", q)
        self.assertIn('email matches regex @"^[^@\\s]+@[^@\\s]+$"', q)
        self.assertIn("pack_array(company_id, source, tolower(email), FindingId)", q)
        self.assertIn('pack_array(url, log_date, device_ip)', q)
        self.assertIn('pack_array(source_name, breach_date, discovery_date)', q)
        self.assertIn('pack_array(keyword, vip_name, source_name, discovery_date)', q)
        self.assertEqual(q.count('iff(alarm_id > 0'), 2)
        self.assertNotRegex(q, r'(?i)\b(?:first|last)\s*=')
        self.assertIn("toupper(severity) == 'LOW', 'Low'", q)

    def test_bicep_and_json_feature_structure_match(self):
        source = (REPO / 'deploy/main.bicep').read_text()
        symbols = set(re.findall(r'^(?:resource|module)\s+(\w+)\s', source, re.M))
        self.assertEqual(symbols, set(self.resources))
        for name in ['sentinelOnboarding', 'alertRules']:
            match = re.search(r"^(?:resource|module) " + name + r" [^\n]+ = if \((\w+) == '([^']+)'\) \{", source, re.M)
            self.assertIsNotNone(match, name)
            variable, value = match.groups()
            self.assertEqual(self.resources[name]['condition'],
                             f"[equals(variables('{variable}'), '{value}')]")
        ci = (REPO / '.github/workflows/ci.yml').read_text()
        self.assertIn('az bicep install --version', ci)
        self.assertIn('az bicep build -f main.bicep --outfile /tmp/expected.json', ci)
        self.assertIn('python3 -m json.tool azuredeploy.json', ci)

    def test_readme_describes_opt_in_and_backfill(self):
        readme = (REPO / 'README.md').read_text()
        self.assertNotIn('incident creation is not offered', readme)
        self.assertNotIn('onboards the workspace to Microsoft Sentinel in every mode', readme)
        self.assertIn('None raises no incident', readme)
        self.assertIn('Direct adds a workspace-scoped Sentinel Responder role and requires Sentinel to be onboarded separately.', readme)
        self.assertIn('last seven days', readme)
        self.assertIn('at most 149 alerts per hour', readme)
        self.assertIn('None leaves onboarding and the enabled rule in place', readme)

    def test_replay_dedup_is_bounded_to_period_and_prior_alerts(self):
        q = self.props['query']
        for fragment in ['TimeGenerated > ago(7d)', 'IngestedAt > ago(7d)',
                         'IngestedAt < ago(12m)', 'EliteDedupKey[0]',
                         'join kind=leftanti Alerted', 'summarize arg_max(TimeGenerated, *) by DedupKey', 'take 149']:
            self.assertIn(fragment, q)
        self.assertEqual(self.props['customDetails']['EliteDedupKey'], 'DedupKey')
        self.assertEqual(self.props['customDetails']['CompanyId'], 'CompanyId')

if __name__ == '__main__':
    unittest.main()
