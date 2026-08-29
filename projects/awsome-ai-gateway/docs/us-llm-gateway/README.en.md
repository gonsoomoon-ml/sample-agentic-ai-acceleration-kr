# AWSome AI Gateway — Global Edition

[한국어](README.md) · **English**

**An LLM gateway that connects in-house Claude Code · Cowork to Amazon Bedrock** — the one-time installation and every update that follows, in one place.
What sets this edition apart: a region outside Korea · direct to Bedrock (not Mantle) · public https entry · English UI. The "US" in update IDs `US-NN` is the **track name** from the first deployment region (us-west-2) — numbering continues even if you change region.

> Synced with the Korean version through `US-09` (2026-08-29). **The linked procedure documents are Korean-only** (install guide, runbooks, update scripts) — this page tells you *what changed* and *whether this deployment has it*; the runbooks are for the operator who performs the change.

**What you want to do**
- **Install for the first time** — POC: [install-overview.md](install-overview.md) (scope · flow, 10 min) → [install-guide.md](install-guide.md) (run §1–§6-0) · production (separate prod account): [ops/8-P-prod.md](ops/8-P-prod.md) — decide which in [1. New-install scope](#1-new-install-scope--what-you-use--poc-or-production) first
- **Already installed — see the update state** — `bash status.sh` on the deployment EC2 → apply only the missing rows of [2. Latest updates](#2-latest-updates) below
- **Set up employee PCs only** — [client-install.md](client-install.md) (Claude Code) · [cowork/…windows.md](cowork/manual/cowork-client-install-windows.md) · [cowork/…windows-auto.md](cowork/manual/cowork-client-install-windows-auto.md) (installer) · [cowork/…macos.md](cowork/cowork-client-install-macos.md) · [cowork/installer/…e2e-windows.md](cowork/installer/cowork-installer-admin-e2e-windows.md) (Windows installer, US-09)

**This deployment**
- 🔴 **Code** — the fork's **`us/deploy-fixes`** branch: https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr/tree/us/deploy-fixes/projects/awsome-ai-gateway (deployment · vendor fixes not yet in upstream [aws-samples](https://github.com/aws-samples/sample-agentic-ai-acceleration-kr); the `forked from aws-samples/…` banner is expected). It is **rebased** onto upstream, so hashes change — versions are counted by **`US-NN`**
- **Region** — `us-west-2` (infrastructure) · inference on **US Geo** (`us.anthropic.*`, spread across us-east-1/2 · us-west-2) · changing region / deploying outside the US: [install-overview §0](install-overview.md#0-이번-배포의-범위-확정) (Korean)
- **Inference backend** — `bedrock-runtime` + US Geo inference profiles (not Mantle)
- **Clients · models** — Claude Code (Mac · Windows · Linux) · Cowork · Opus 4.8 · Sonnet 5 · Haiku 4.5 · Opus 5 — a POC adds Cowork · Opus 5 via `US-02`; production (`US-08`) includes them
- **Entry point** — POC: http ALB + IP allow-list (mode A), https (`US-06`) with a domain · production (`US-08`): https domain + both admin ALBs internal (site-to-site VPN)

---

## 1. New-install scope — what you use · POC or production

**POC and production are different stacks, not options on one stack** — production is not a change to dev but a **new stack in a separate account with `environment=prod`** (`US-08`).

| | POC (dev) | Production (prod) |
|---|---|---|
| Account · sizing | one account · `environment=dev` (Aurora ×1 · Valkey ×1 · NAT ×1) | **separate account** · `environment=prod` (Aurora ×2 · Valkey 3 shards × 3 · NAT ×2) |
| Entry point | http ALB + IP allow-list (mode A) | https domain (`US-06`) + both admin ALBs internal (`US-07`, requires a site-to-site VPN) |
| Procedure | `US-01` — [install-guide.md](install-guide.md) §1–§6 | **`US-08`** — [ops/8-P-prod.md](ops/8-P-prod.md) (dev stays as is) |

| Your setup | POC (dev) | Production (prod) |
|---|---|---|
| Claude Code only (Opus 4.8 · Sonnet 5 · Haiku 4.5) | `US-01` | `US-08` |
| Claude Code only + **Opus 5** | `US-01` + step `02` of `US-02` (model registration) | `US-08` |
| Claude Code + **Cowork** | `US-01` + all of `US-02` (`01` routing · `02` model · `03` CloudFront) | `US-08` |

- **Production (`US-08`)** — includes https (US-06) · admin internal (US-07) · Cowork routing · Opus 5 from the start. `US-03·04·05` are included in new installs (required).
- **POC (`US-01`) only:**
  - **`US-06` (ALB HTTPS)** — Cowork requires https: CloudFront (`03`) without a domain, US-06 with one — never both. If a domain arrives later, follow the [switch runbook](ops/8-H-alb-https.md).
  - **`US-07` (admin ALBs internal)** — the final posture for production with a site-to-site VPN; usually not needed in a POC. To apply it, follow the [switch runbook](ops/8-I-admin-internal.md) — internal without a VPN blocks VK issuance. Production assumes the VPN ([8-P §0](ops/8-P-prod.md)).
  - ⚠️ **`US-02` is required even for a new POC install** — the migration seeds the Cowork routing row with a non-existent account, so left as is every Cowork request is a 502. Claude Code only + seed models → can be skipped.

---

## 2. Latest updates

**Newest 5 only** — full history (US-01~) and the why · pitfalls per item: [updates.en.md](updates.en.md). `US-NN` is a fixed ID unaffected by rebases. **Check the current state first with [3. Applying updates](#3-applying-updates-on-the-deployment-ec2).**

| ID (doc) | What | Grade · new installs | Existing deployments do |
|---|---|---|---|
| [**US-09**](cowork/installer/cowork-installer-admin-e2e-windows.md) 2026/08 | Cowork Windows installer — admin builds one .exe → installs on employee PCs (HKLM policy) | Optional · recommended for Cowork on Windows (replaces manual setup) · no gateway change | on a build PC clone `feat/cowork-installer-import` → `site-config.json` from `07-client-values.sh` → `build.ps1` → install + `setup` on employee PCs |
| [**US-08**](ops/8-P-prod.md) 2026/08 | New prod stack — separate account · https + admin internal + VPN · Cowork Windows | Optional · when moving from POC to production · `environment=prod` | leave dev as is; rerun §1–§6 in the prod account (8-P order) |
| [**US-07**](ops/8-I-admin-internal.md) 2026/08 | Customer final architecture — both admin ALBs internal (private subnets) | Optional · requires site-to-site VPN · new POC installs: at §3-6 via values · production (`US-08`) includes it | uncomment 2 values blocks → helm (ALB recreation) → swap admin SG · CNAMEs |
| [**US-06**](ops/8-H-alb-https.md) 2026/08 | ALB HTTPS — custom domain + ACM | Optional · POC with a domain · production (`US-08`) includes it | get a domain → switch → update 2 client URLs (30 min) |
| [**US-05**](ops/8-E-eks-upgrade.md) 2026/08 | EKS 1.31 → 1.34 | Required (support expiry · cost) · included in new installs | apply one minor at a time ×3 + restart all ns |
Earlier (`US-01` initial install) and the why · pitfalls per item → [updates.en.md](updates.en.md)

---

## 3. Applying updates (on the deployment EC2)

**① Bring the repository up to date** — a rebased branch, so not `git pull` but the block below. `values-*.yaml` exists only on this EC2, so the backup · restore is the point (confirm `values restored OK`). `origin` in `git remote -v` must be `gonsoomoon-ml/…` (if it is aws-samples, `set-url`). The prod stack (`US-08`) follows the same steps on the **prod account's deployment EC2** with `V=…/values-eks-fargate-prod.yaml` — `status.sh` does not judge US-08~09 (US-08 is a separate stack, US-09 is PC-side).

```bash
cd ~/awsome-ai-gateway && git remote -v
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
cp $V ~/values.bak && git fetch origin
git reset --hard origin/us/deploy-fixes && cp ~/values.bak $V
cmp -s $V ~/values.bak && echo "values restored OK" || echo "RESTORE FAILED"
```

**② Check the state** — queries the live system (DB rows · endpoints · image · ALB), changes nothing, 1–2 min (throwaway psql pod). Output is Korean; markers `OK` applied · `!!` partial · `XX` missing · `--` optional. Raw evidence: `--verbose`.

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts && bash status.sh
```
```
   OK   US-01  최초 설치 (기준선)
   !!   US-02  Cowork 연결 + Opus 5 등록 — 일부 적용   routing OK · opus-5 OK · CloudFront 없음
   XX   US-04  Bedrock·STS VPC Endpoint — 미적용 (필수)
   --   US-06  ALB HTTPS (커스텀 도메인) — 미적용 (선택 · 운영이면 권장)
 다음 작업: bash 03-create-cloudfront.sh … / (수동) ops/8-N-vpc-endpoint.md …
```

**③ Only the missing rows**, via the doc column of the table in §2. Detailed procedure · pitfalls · rollback: [ops/8-U-update.md](ops/8-U-update.md).

---

What the system does (auth · budget · rate limit · inference · accounting) and the diagram → [architecture.md](architecture.md) "전체 그림" · production diagram [8-P §1](ops/8-P-prod.md) · requirements [prd.md](prd.md) · operations [operations.md](operations.md) (all Korean)
