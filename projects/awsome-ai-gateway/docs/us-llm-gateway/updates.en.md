# Update history (US-NN)

[한국어](updates.md) · **English**

README's "Latest updates" shows only the newest 5 — this is the full history. `US-NN` is a fixed ID unaffected by rebases. Check what is applied with `bash status.sh` on the deployer EC2.
Grades — **Required** · **Recommended** (feature does not work without it) · **Optional** (on request)

| ID (doc) | What | Grade · new installs | Existing deployments do |
|---|---|---|---|
| [**US-08**](ops/8-L-admin-ui-login.md) 2026/08 | Admin UI Cognito login — replaces dev-login | Optional · **strongly recommended for production** · included in new installs (apply separately) | rebuild images → `setup-admin-ui-login.sh` → install-eks → `devLoginEnabled=false` |
| [**US-07**](ops/8-I-admin-internal.md) 2026/08 | Customer final architecture — both admin ALBs internal (private subnets) | Optional · requires site-to-site VPN · new installs: at §3-6 via values | uncomment 2 values blocks → helm (ALB recreation) → swap admin SG · CNAMEs |
| [**US-06**](ops/8-H-alb-https.md) 2026/08 | ALB HTTPS — custom domain + ACM certificate | Optional · **strongly recommended for production** · new installs: same steps at §3-6 | get a domain → switch → update 2 client URLs (~30 min) |
| [**US-05**](ops/8-E-eks-upgrade.md) 2026/08 | EKS 1.31 → 1.34 | Required (support expiry · cost) · included in new installs | apply one minor at a time ×3 + restart pods in all namespaces |
| [**US-04**](ops/8-N-vpc-endpoint.md) 2026/08 | Bedrock · STS over VPC Endpoints instead of NAT | Required (compliance) · included in new installs | apply endpoints → restart gateway-proxy |
| [**US-03**](ops/8-U-update.md) 2026/08 | Admin UI KO/EN toggle | Required (English support) · included in new installs | rebuild admin-ui image → install-eks |
| [**US-02**](update-scripts/README.md#실행-순서) 2026/08 | Cowork connection + Opus 5 registration | Per item — Cowork needs `01`·`03`, Opus 5 needs `02` · 🔴 **new installs too** | 01 routing · 02 model (pricing required) · 03 CloudFront (only without a domain) |
| [**US-01**](install-overview.md) 2026/07 | Initial install (baseline) | — | — |

## Why · pitfalls (per item)

- **US-08** — Replaces dev-login (a role-select MVP bypass that issues an unsigned cookie) with a real Cognito login (email/password) on admin-ui. The `ClaudeAdmin` group still auto-grants ADMIN, but TEAM_LEADER is no longer a Cognito group — an admin assigns it manually from the admin-ui `/users` screen instead (avoids the operational overhead and mis-assignment risk of keeping two group memberships in sync). Requires issuing a new RSA keypair for session signing (`admin-api/scripts/generate_admin_jwt_keypair.py`); `deployment/scripts/setup-admin-ui-login.sh <env>` automates key generation, the DB update (`auth.admin_jwt_configs`), Secret update, and the values-file patch. dev-login can stay on via `global.devLoginEnabled` while you verify real login, then be turned off.
- **US-07** — Final customer posture: move the control-plane ALBs (admin-api · admin-ui) to internal in the private subnets, reachable only over the site-to-site VPN; the data plane (gateway) stays public. No terraform change — the vpc module already creates the subnets and tags. New installs include it during `US-01` (uncomment the values at §3-6, no extra steps — see the note in install-guide §3-6); running deployments follow the [§8-I runbook](ops/8-I-admin-internal.md) — an ALB recreation, so swap the admin CNAMEs (a few minutes of admin downtime). ⚠️ Applying without the VPN breaks VK issuance (api-key-helper → admin-api) and thus the gateway itself — do not apply before the VPN exists. Internal ALB creation and in-VPC reachability were verified by rehearsal (2026-08-20).
- **US-06** — Serve the 3 ALBs as `https://gateway-<env>.<domain>` instead of http:80 on the temporary ALB address: TLS terminated at ACM, a fixed name, no CloudFront needed for Cowork. If your account cannot register domains (e.g. Amazon-internal), register in another account and delegate NS. Afterwards replace `ANTHROPIC_BASE_URL` · `ADMIN_API_URL` on clients.
- **US-05** — 1.31 is past standard support: extended-support surcharge (~$365/month per cluster) and after end of support (2026-11-26) AWS force-upgrades automatically. One minor version at a time (3 applies); restart pods in every namespace after each step (on Fargate a pod is a node).
- **US-04** — Bedrock · STS calls go over PrivateLink inside the VPC instead of NAT + public internet. Only VPCs created before the endpoint declaration existed are affected (new installs already include it) — nothing tells you, Bedrock keeps working. Restart gateway-proxy right after applying: dead sockets left in the pool cause consecutive 502s that look like the endpoint is broken.
- **US-03** — Admin console i18n; the KO/EN toggle in the header actually translates. Needs an admin-ui image rebuild.
- **US-02** — The install migration seeds the Cowork routing row with a non-existent account, so left as is every Cowork request is a 502 (`01`). `02` model registration is also needed for Claude Code when you use Opus 5 (the install seed has no Opus 5) — skip the pricing and cost is recorded as `$0`, bypassing budgets. `03` CloudFront only when you need https for Cowork without a domain (not needed with US-06). Claude Code only + seed models → all of US-02 can be skipped.
- **US-01** — Baseline: single account · us-west-2 · Claude Code · US Geo inference.
