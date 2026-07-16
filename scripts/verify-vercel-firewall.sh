#!/bin/sh
set -eu

scope="${VERCEL_SCOPE:-buildproven}"
emergency_status="${VERCEL_EMERGENCY_STATUS:-Disabled}"
rate_rule="rule_rate_limit_lifescape_hosted_runs_MaiEPl"
emergency_rule="rule_emergency_disable_lifescape_hosted_runs_s3mPXu"

case "$emergency_status" in
  Enabled|Disabled) ;;
  *)
    echo "VERCEL_EMERGENCY_STATUS must be Enabled or Disabled." >&2
    exit 1
    ;;
esac

command -v vercel >/dev/null 2>&1 || {
  echo "Vercel CLI is required to verify hosted controls." >&2
  exit 1
}

rate_output="$(vercel firewall rules inspect "$rate_rule" --scope "$scope" 2>&1)"
printf "%s\n" "$rate_output" | grep -F "Status:      Enabled" >/dev/null
printf "%s\n" "$rate_output" | grep -F "path equals /api/run" >/dev/null
printf "%s\n" "$rate_output" | grep -F "Rate Limit (10/60s)" >/dev/null
printf "%s\n" "$rate_output" | grep -F "Keys:         ip" >/dev/null

emergency_output="$(vercel firewall rules inspect "$emergency_rule" --scope "$scope" 2>&1)"
printf "%s\n" "$emergency_output" | grep -F "Status:      $emergency_status" >/dev/null
printf "%s\n" "$emergency_output" | grep -F "path equals /api/run" >/dev/null
printf "%s\n" "$emergency_output" | grep -F "Action:      Deny" >/dev/null

diff_output="$(vercel firewall diff --scope "$scope" 2>&1)"
printf "%s\n" "$diff_output" | grep -F "No pending changes." >/dev/null

echo "Vercel hosted controls verified."
