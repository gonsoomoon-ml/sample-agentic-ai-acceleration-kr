# Update history (US-NN)

[한국어](updates.md) · **English**

README's "Latest updates" shows only the newest 5 — this is the full history. `US-NN` is a fixed ID unaffected by rebases. Check what is applied with `bash status.sh` on the deployer EC2.
Grades — **Required** · **Recommended** (feature does not work without it) · **Optional** (on request)

| ID | What | Grade | New installs | Existing deployments do | Doc |
|---|---|---|---|---|---|
| `US-06` 2026/08 | ALB HTTPS — custom domain + ACM certificate | Optional · **strongly recommended for production** | same steps at §3-6 | get a domain → switch → update 2 client URLs (~30 min) | [ops/8-H](ops/8-H-alb-https.md) |
| `US-05` 2026/08 | EKS 1.31 → 1.34 | Required (support expiry · cost) | included | apply one minor at a time ×3 + restart pods in all namespaces | [ops/8-E](ops/8-E-eks-upgrade.md) |
| `US-04` 2026/08 | Bedrock · STS over VPC Endpoints instead of NAT | Required (compliance) | included | apply endpoints → restart gateway-proxy | [ops/8-N](ops/8-N-vpc-endpoint.md) |
| `US-03` 2026/08 | Admin UI KO/EN toggle | Required (English support) | included | rebuild admin-ui image → install-eks | [ops/8-U](ops/8-U-update.md) |
| `US-02` 2026/08 | Cowork connection + Opus 5 registration | Per item — Cowork needs `01`·`03`, Opus 5 needs `02` | 🔴 **new installs too** | 01 routing · 02 model (pricing required) · 03 CloudFront (only without a domain) | [update-scripts](update-scripts/README.md#실행-순서) |
| `US-01` 2026/07 | Initial install (baseline) | — | — | — | [install-overview](install-overview.md) |

## Why · pitfalls (per item)

- **US-06** — Serve the 3 ALBs as `https://gateway-<env>.<domain>` instead of http:80 on the temporary ALB address: TLS terminated at ACM, a fixed name, no CloudFront needed for Cowork. If your account cannot register domains (e.g. Amazon-internal), register in another account and delegate NS. Afterwards replace `ANTHROPIC_BASE_URL` · `ADMIN_API_URL` on clients.
- **US-05** — 1.31 is past standard support: extended-support surcharge (~$365/month per cluster) and after end of support (2026-11-26) AWS force-upgrades automatically. One minor version at a time (3 applies); restart pods in every namespace after each step (on Fargate a pod is a node).
- **US-04** — Bedrock · STS calls go over PrivateLink inside the VPC instead of NAT + public internet. Only VPCs created before the endpoint declaration existed are affected (new installs already include it) — nothing tells you, Bedrock keeps working. Restart gateway-proxy right after applying: dead sockets left in the pool cause consecutive 502s that look like the endpoint is broken.
- **US-03** — Admin console i18n; the KO/EN toggle in the header actually translates. Needs an admin-ui image rebuild.
- **US-02** — The install migration seeds the Cowork routing row with a non-existent account, so left as is every Cowork request is a 502 (`01`). `02` model registration is also needed for Claude Code when you use Opus 5 (the install seed has no Opus 5) — skip the pricing and cost is recorded as `$0`, bypassing budgets. `03` CloudFront only when you need https for Cowork without a domain (not needed with US-06). Claude Code only + seed models → all of US-02 can be skipped.
- **US-01** — Baseline: single account · us-west-2 · Claude Code · US Geo inference.
