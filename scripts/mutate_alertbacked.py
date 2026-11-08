"""Bounded direct mutations of the shipped template, restored after every run."""
import copy
import json
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
path = REPO / 'deploy/azuredeploy.json'
original = path.read_bytes()
template = json.loads(original)
def rule(t):
    return t['resources']['alertRules']['properties']['template']['resources'][0]['properties']
def set_default(t): t['parameters']['IncidentMode']['defaultValue'] = 'AlertBacked'
def always_on(t): rule(t)['enabled'] = True
def legacy(t): t['variables']['effectiveIncidentMode'] = "[parameters('IncidentMode')]"
def period(t): rule(t)['queryPeriod'] = 'P1D'
def entity(t): rule(t)['entityMappings'] = []
def table(t): rule(t)['query'] = rule(t)['query'].replace('SOCRadar_Botnet_CL', 'SOCRadar_Botnet_BROKEN_CL')
def company(t): rule(t)['query'] = rule(t)['query'].replace('pack_array(company_id, source,', 'pack_array(source,')
def dedup(t): rule(t)['query'] = rule(t)['query'].replace('join kind=leftanti', 'join kind=inner')
def matched(t): rule(t)['query'] = rule(t)['query'].replace("| where tolower(entra_status) in ('found', 'compromised')", '')
def email(t): rule(t)['query'] = rule(t)['query'].replace('| where email matches regex @"^[^@\\s]+@[^@\\s]+$"', '')
mutants = [('default on', set_default), ('always enabled', always_on), ('legacy ignored', legacy),
           ('period shortened', period), ('entity removed', entity), ('wrong table', table),
           ('company isolation removed', company), ('anti-join reversed', dedup),
           ('unconfirmed match admitted', matched), ('invalid account admitted', email)]
blind = []
baseline = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_alertbacked.py'],
                          cwd=REPO / 'FunctionApp', capture_output=True, text=True, timeout=60)
if baseline.returncode:
    sys.exit('Baseline contract must pass before mutation')
try:
    for name, change in mutants:
        candidate = copy.deepcopy(template)
        change(candidate)
        path.write_text(json.dumps(candidate, indent=2))
        result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_alertbacked.py'],
                                cwd=REPO / 'FunctionApp', capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            blind.append(name)
        print(('BLIND' if result.returncode == 0 else 'caught'), name, flush=True)
        path.write_bytes(original)
finally:
    path.write_bytes(original)
print('blind:', len(blind), 'of', len(mutants))
sys.exit(bool(blind))
