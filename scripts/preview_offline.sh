#!/usr/bin/env bash
# Run `pulumi preview` with a throw-away local backend and fake AWS credentials.
# It checks that the program runs in the real Pulumi engine and that every
# resource input is valid for the provider schema. Nothing is sent to AWS.
set -euo pipefail

state_dir="$(mktemp -d)"
trap 'rm -rf "$state_dir"; rm -f Pulumi.ci.yaml' EXIT

export PULUMI_BACKEND_URL="file://$state_dir"
export PULUMI_CONFIG_PASSPHRASE="offline-preview"
export PULUMI_SKIP_UPDATE_CHECK=1
export AWS_ACCESS_KEY_ID="AKIAOFFLINEPREVIEW00" AWS_SECRET_ACCESS_KEY="offline"

pulumi stack init ci --non-interactive >/dev/null
pulumi config set aws:region us-east-1
for flag in skipCredentialsValidation skipRequestingAccountId skipMetadataApiCheck skipRegionValidation; do
  pulumi config set "aws:$flag" true
done
pulumi preview --non-interactive --diff=false
