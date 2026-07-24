# Copyright 2026 (c) Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.
#
# LLM Gateway onboarding (Windows PowerShell) - verify gateway-cli + OIDC login + (optional) Claude Code integration.
# Automates the common "step 1" shared by all three clients. For Codex/Cowork setup see guides\QUICKSTART.md.
#
# Usage (PowerShell):
#   $env:OIDC_ISSUER_URL="..."; $env:OIDC_CLIENT_ID="..."; $env:ADMIN_API_URL="..."; $env:ANTHROPIC_BASE_URL="..."
#   .\scripts\onboard-windows.ps1 [-SetupClaudeCode]
#
# Safety: by default only runs gateway-cli login (writes to %USERPROFILE%\.gateway-cli only).
#         Claude Code settings are changed (gateway-cli setup) only when -SetupClaudeCode is given.
#
# NOTE: keep this script ASCII-only. Windows PowerShell 5.1 reads BOM-less files as system ANSI,
#       so non-ASCII (e.g. Korean) comments corrupt and break parsing. English/ASCII avoids this.
param([switch]$SetupClaudeCode)
$ErrorActionPreference = "Stop"

function Log($m) { Write-Host "[onboard] $m" -ForegroundColor Cyan }
function ErrLog($m) { Write-Host "[onboard:err] $m" -ForegroundColor Red }

# 0. Check required env vars
$need = "OIDC_ISSUER_URL","OIDC_CLIENT_ID","ADMIN_API_URL","ANTHROPIC_BASE_URL"
$missing = $need | Where-Object { -not [Environment]::GetEnvironmentVariable($_) }
if ($missing) {
  ErrLog "Missing required environment variables (ask your operator): $($missing -join ', ')"
  ErrLog 'e.g.) $env:OIDC_ISSUER_URL="..." ; $env:OIDC_CLIENT_ID="..." ; $env:ADMIN_API_URL="..." ; $env:ANTHROPIC_BASE_URL="..."'
  exit 1
}

# 1. Verify gateway-cli is installed (Windows assumes pre-install via operator package / uv)
if (Get-Command gateway-cli -ErrorAction SilentlyContinue) {
  Log "gateway-cli found: $(gateway-cli version 2>$null)"
} else {
  ErrLog "gateway-cli not installed. Install it first:"
  ErrLog '  option A) Invoke-WebRequest -Uri <URL> -OutFile gw.zip; Expand-Archive gw.zip -DestinationPath "$env:ProgramFiles\GatewayCLI"; add to PATH'
  ErrLog '  option B) uv tool install --from .\gateway-cli gateway-cli'
  exit 1
}

# 2. Gateway health check (non-fatal)
try {
  $r = Invoke-WebRequest -Uri "$($env:ANTHROPIC_BASE_URL.TrimEnd('/'))/health" -UseBasicParsing -TimeoutSec 10
  Log "gateway health: $($r.StatusCode)"
} catch { ErrLog "gateway health check failed (continuing): $($_.Exception.Message)" }

# 3. OIDC login
Log "OIDC login - a browser window will open..."
gateway-cli login --issuer-url $env:OIDC_ISSUER_URL --client-id $env:OIDC_CLIENT_ID
Log "Login complete. Token cache: $env:USERPROFILE\.gateway-cli\oidc-tokens.json"

# 4. (Optional) Claude Code integration
if ($SetupClaudeCode) {
  Log "Claude Code integration (gateway-cli setup)"
  gateway-cli setup --gateway-url $env:ANTHROPIC_BASE_URL --admin-api-url $env:ADMIN_API_URL
  Log "Done. Restart Claude Code and run claude. To revert: gateway-cli disable"
} else {
  Log "Common step 1 (login) complete. Re-run with -SetupClaudeCode for automatic Claude Code integration."
  Log "For Codex/Cowork see guides\QUICKSTART.md. For Cowork/bulk deploy use operator MDM/.reg."
}
