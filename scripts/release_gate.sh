#!/usr/bin/env bash
# Install the shipped artefacts into a throwaway resource group and check that
# the result is actually usable, then delete it.
#
# Run this after the code is frozen and the release asset is uploaded, before
# announcing the release. Everything else in this repository tests the code
# against itself. This is the only step that answers the question a customer
# asks by clicking Deploy, and it is the only thing that has ever caught a
# template output claiming success it had not achieved.
#
#   scripts/release_gate.sh
#   scripts/release_gate.sh --keep        # leave the group for inspection
#
# Not in CI on purpose: it creates real resources and costs real money, and
# this project has a standing rule about leaving test resources running.

set -euo pipefail

KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/deploy/azuredeploy.json"
STAMP="$(date -u +%m%d%H%M)"
RG="gate-$STAMP"
LOCATION="${LOCATION:-northeurope}"

pass() { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILED=1; }
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }
FAILED=0

cleanup() {
  local code=$?
  if [ "$KEEP" = "1" ]; then
    printf '\n--keep: %s left in place. Delete it when you are done.\n' "$RG"
    return $code
  fi
  step "Cleaning up"
  # Runs on every exit path, including the failed deployment that used to
  # leave the key file behind.
  [ -n "${PARAMS:-}" ] && rm -f "$PARAMS"
  if az group show -n "$RG" >/dev/null 2>&1; then
    az group delete -n "$RG" --yes --no-wait -o none 2>/dev/null || true
    # Confirm the delete was accepted rather than assuming it. A group left
    # behind here bills until someone notices.
    sleep 10
    if az group show -n "$RG" --query properties.provisioningState -o tsv 2>/dev/null | grep -q Deleting; then
      pass "$RG is being deleted"
    elif ! az group show -n "$RG" >/dev/null 2>&1; then
      pass "$RG is gone"
    else
      fail "$RG still exists — DELETE IT BY HAND: az group delete -n $RG --yes"
    fi
  fi
  return $code
}
trap cleanup EXIT

step "Preflight"
command -v az >/dev/null || { echo "az not found"; exit 1; }
SUB=$(az account show --query name -o tsv) || { echo "run: az login"; exit 1; }
pass "subscription: $SUB"

# az account show serves a cached value, so it says nothing about whether the
# subscription can still take writes. A Warned (read-only) subscription fails
# the deployment below, and that failure reads exactly like a broken template
# -- an afternoon was once lost to that misattribution.
SUB_ID=$(az account show --query id -o tsv)
SUB_STATE=$(az rest --method GET \
  --uri "https://management.azure.com/subscriptions/$SUB_ID?api-version=2022-12-01" \
  --query state -o tsv 2>/dev/null || echo "unreadable")
[ "$SUB_STATE" = "Enabled" ] && pass "subscription state: Enabled (read from ARM)" \
  || { fail "subscription state is '$SUB_STATE', not Enabled — a deployment failure here would not be about the code"; exit 1; }

# Set GATE_PACKAGE_URI to test a package that is not published yet. Without it
# the gate installs whatever the template points at, which is the code already
# in customers' hands — useful for checking a template change, useless for
# checking new code. Uploading first and testing afterwards would mean every
# running installation picks up untested code the moment it restarts, because
# the release URL never changes (product-policy §1).
PKG=$(python3 -c "import json;print(json.load(open('$TEMPLATE'))['parameters']['PackageUri']['defaultValue'])")
if [ -n "${GATE_PACKAGE_URI:-}" ]; then
  PKG="$GATE_PACKAGE_URI"
  printf '  \033[33mNOTE\033[0m candidate package, not the published one\n'
fi
CODE=$(curl -sIL -o /dev/null -w '%{http_code}' "$PKG")
[ "$CODE" = "200" ] && pass "package reachable: ${PKG##*/}" \
                    || { fail "package returns HTTP $CODE — upload the asset first"; exit 1; }

: "${GATE_COMPANY_ID:?set GATE_COMPANY_ID}"
: "${GATE_API_KEY:?set GATE_API_KEY (never commit it)}"
: "${GATE_APP_ID:?set GATE_APP_ID (existing multi-tenant App Registration)}"
TENANT=$(az account show --query tenantId -o tsv)

step "Deploying $RG"
az group create -n "$RG" -l "$LOCATION" -o none
# The parameter file holds a live customer API key. Written under the default
# umask into a predictable path it was world-readable, and the delete below is
# skipped on the failure path, so a failed gate run left the key in /tmp for
# good. Create it private, and let the exit trap remove it either way.
PARAMS="$(mktemp "${TMPDIR:-/tmp}/gate-params.XXXXXX")"
chmod 600 "$PARAMS"
cat > "$PARAMS" <<JSON
{
  "FormerCompanies": { "value": { "rows": [
    { "companyId": "$GATE_COMPANY_ID", "tenantIds": "$TENANT",
      "apiKey": "$GATE_API_KEY", "actorEmail": "${GATE_ACTOR_EMAIL:-gate@example.com}" }
  ] } },
  "EntraIdClientId": { "value": "$GATE_APP_ID" },
  "EnableLeakMonitoring": { "value": true },
  "LeakResponse": { "value": "logOnly" },
  "FormerClientMode": { "value": "mock" },
  "FormerApplyChanges": { "value": false },
  "PackageUri": { "value": "$PKG" }
}
JSON
# logOnly and mock on purpose: this gate proves the install works, it is not a
# licence to mutate somebody's directory.

if az deployment group create -g "$RG" -n gate --template-file "$TEMPLATE" \
     --parameters "@$PARAMS" -o none 2>/tmp/"$RG".err; then
  pass "deployment succeeded"
else
  fail "deployment failed"
  head -20 "/tmp/$RG.err"
  exit 1
fi

step "Checking what the deployment reported"
OUTPUTS=$(az deployment group show -g "$RG" -n gate --query properties.outputs -o json)
FIC_NOTE=$(python3 -c "import json,sys;print((json.loads(sys.argv[1]) or {}).get('ficNote',{}).get('value',''))" "$OUTPUTS")
case "$FIC_NOTE" in
  *"ACTION REQUIRED"*|*"CHECK THIS"*)
      fail "the federated credential is not confirmed:"
      printf '       %s\n' "$FIC_NOTE" ;;
  "") fail "no ficNote in the outputs" ;;
  *)  pass "ficNote: ${FIC_NOTE:0:60}..." ;;
esac

APP=$(az functionapp list -g "$RG" --query "[0].name" -o tsv)
[ -n "$APP" ] && pass "function app: $APP" || { fail "no function app"; exit 1; }

step "Waiting for the host to index the functions"
KEY=$(az functionapp keys list -g "$RG" -n "$APP" --query masterKey -o tsv)
COUNT=0
for _ in $(seq 1 20); do
  COUNT=$(curl -s -H "x-functions-key: $KEY" "https://$APP.azurewebsites.net/admin/functions" \
          | python3 -c "import json,sys;print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
  [ "$COUNT" -ge 6 ] && break
  sleep 15
done
# A package the Linux worker cannot read indexes zero functions while the host
# still reports Running, so the count is the only honest signal here.
[ "$COUNT" -ge 6 ] && pass "$COUNT functions indexed" \
                   || fail "only $COUNT functions indexed — check the package entry permissions"

step "Asking the app whether it can do its job"
PREVIEW=$(curl -s -H "x-functions-key: $KEY" \
  "https://$APP.azurewebsites.net/api/leak/preview?company_id=$GATE_COMPANY_ID" || echo '{}')
python3 - "$PREVIEW" <<'PY' && pass "leak/preview reports a reachable tenant" || fail "leak/preview: no tenant reachable (the app cannot get a Graph token)"
import json, sys
try:
    d = json.loads(sys.argv[1])
except Exception:
    sys.exit(1)
companies = d.get("companies") or []
sys.exit(0 if any(c.get("tenants_reachable") for c in companies) else 1)
PY

FORMER=$(curl -s -o /dev/null -w '%{http_code}' -H "x-functions-key: $KEY" \
  "https://$APP.azurewebsites.net/api/former/preview")
[ "$FORMER" = "200" ] && pass "former/preview answers" || fail "former/preview returned HTTP $FORMER"

step "Result"
if [ "$FAILED" = "0" ]; then
  printf '  \033[32mThe shipped artefacts install and work. Safe to announce.\033[0m\n'
else
  printf '  \033[31mDo not announce this release.\033[0m\n'
fi
exit "$FAILED"
