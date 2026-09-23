# AWSome AI Gateway — Global Edition

[한국어](README.md) · **English**

**An LLM gateway that connects in-house Claude Code · Cowork to Amazon Bedrock** — the one-time installation and every update that follows, in one place.
What sets this edition apart: a region outside Korea · direct to Bedrock (not Mantle) · public https entry · English UI. The "US" in update IDs `US-NN` is the **track name** from the first deployment region (us-west-2) — numbering continues even if you change region.

> Synced with the Korean version through `US-09` (2026-08-29). **The linked procedure documents are Korean-only** (install guide, runbooks, update scripts) — this page tells you *what changed* and *whether this deployment has it*; the runbooks are for the operator who performs the change.

**What you want to do**
- **Install for the first time** — POC: [install-overview.md](install-overview.md) (scope · flow, 10 min) → [install-guide.md](install-guide.md) (run §1–§6-0) · production (separate prod account): [ops/8-P-prod.md](ops/8-P-prod.md) drives [install-guide.md](install-guide.md) §1–§6 in the prod account — decide which in [1. New-install scope](#1-new-install-scope--what-you-use--poc-or-production) first
- **Already installed — see the update state** — `bash status.sh` on the deployment EC2 → apply only the missing rows of [2. Latest updates](#2-latest-updates) below · `US-10`·`US-11` are not judged by `status.sh` — check them with `bash 14-postdeploy-check.sh` (DB schema number · prices match)
- **Set up employee PCs only** — [client-install.md](client-install.md) (Claude Code) · [cowork/…windows.md](cowork/manual/cowork-client-install-windows.md) · [cowork/…windows-auto.md](cowork/manual/cowork-client-install-windows-auto.md) (installer) · [cowork/…macos.md](cowork/cowork-client-install-macos.md) · [cowork/installer/…e2e-windows.md](cowork/installer/cowork-installer-admin-e2e-windows.md) (Windows installer, US-09)

**This deployment**
- 🔴 **Code** — the fork's **`us/deploy-fixes`** branch: https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr/tree/us/deploy-fixes/projects/awsome-ai-gateway (deployment · vendor fixes not yet in upstream [aws-samples](https://github.com/aws-samples/sample-agentic-ai-acceleration-kr); the `forked from aws-samples/…` banner is expected). It is **rebased** onto upstream, so hashes change — versions are counted by **`US-NN`**
- **Region** — `us-west-2` (infrastructure) · inference on **US Geo** (`us.anthropic.*`, spread across us-east-1/2 · us-west-2) · changing region / deploying outside the US: [install-overview §0](install-overview.md#0-이번-배포의-범위-확정) (Korean)
- **Inference backend** — `bedrock-runtime` + US Geo inference profiles (not Mantle)
- **Clients · models** — Claude Code (Mac · Windows · Linux) · Cowork · Opus 5 · Opus 4.8 · Sonnet 5 · Haiku 4.5 — all included in `US-01`; production (`US-08`) includes them too
- **Entry point** — POC: http ALB + IP allow-list (mode A), https (`US-06`) with a domain · production (`US-08`): https domain + both admin ALBs internal (site-to-site VPN)

---

## 1. New-install scope — what you use · POC or production

**POC and production are different stacks, not options on one stack** — production is not a change to dev but a **new stack in a separate account with `environment=prod`** (`US-08`).

| | POC (dev) | Production (prod) |
|---|---|---|
| Account · sizing | one account · `environment=dev` (Aurora ×1 · Valkey ×1 · NAT ×1) | **separate account** · `environment=prod` (Aurora ×2 · Valkey 3 shards × 3 · NAT ×2) |
| Entry point | http ALB + IP allow-list (mode A) | https domain (`US-06`) + both admin ALBs internal (`US-07`, requires a site-to-site VPN) |
| Procedure | `US-01` — [install-guide.md](install-guide.md) §1–§6 | **`US-08`** — the same install as `US-01`, done in the prod account by following [ops/8-P-prod.md](ops/8-P-prod.md), which tells you at which steps to add the prod settings (https · admin internal · VPN) (dev stays as is) |

| Your setup | POC (dev) | Production (prod) |
|---|---|---|
| Claude Code only (Opus 5 · Opus 4.8 · Sonnet 5 · Haiku 4.5) | `US-01` | `US-08` (the same install as `US-01`, in the prod account per 8-P — https · admin internal · VPN included) |
| Claude Code + **Cowork** | `US-01` + one https entry (`US-06` with a domain, otherwise `03` CloudFront from `US-02`) | `US-08` (same; https included, so no entry choice) |

- **Production (`US-08`)** — the same install as `US-01` with https (US-06) · admin internal (US-07) · VPN · prod sizing added from the start.
- **Already included in every new install (POC and production) — nothing to apply separately**:
  - **`US-03·04·05`** — admin UI KO/EN toggle · Bedrock VPC endpoints · EKS 1.34 are part of the install steps.
  - **`US-10`** — the code you install is US-10: latest DB schema · stability fixes · web-search cost caps and improvements work by default.
  - **`US-11`** — install-guide §4-2 (C) seeds the prices from `update-scripts/pricing.tsv` (default = `us.` Standard tier). **If you are billed at another region or tier**, edit that file to your billed prices before §4-2 (how: see the note in install-guide §4-2 (C)).
- **POC (`US-01`) only:**
  - **`US-06` (ALB HTTPS)** — Cowork requires https: CloudFront (`03`) without a domain, US-06 with one — never both. If a domain arrives later, follow the [switch runbook](ops/8-H-alb-https.md).
  - **`US-07` (admin ALBs internal)** — the final posture for production with a site-to-site VPN; usually not needed in a POC. To apply it, follow the [switch runbook](ops/8-I-admin-internal.md) — internal without a VPN blocks VK issuance. Production assumes the VPN ([8-P §0](ops/8-P-prod.md)).
  - **`US-02` is for existing deployments only** — a new install already gets the same content from §4-2 (Opus 5) and §4-3 (Cowork routing). The only piece left for a new install is `03` CloudFront, when Cowork is used without a domain.

---

## 2. Latest updates

**Newest 5 only** — full history (US-01~) and the why · pitfalls per item: [updates.en.md](updates.en.md). `US-NN` is a fixed ID unaffected by rebases. **Check the current state first with [3. Applying updates](#3-applying-updates-on-the-deployment-ec2).**

| ID (doc) | What | Grade · installing new | Already installed — how to apply |
|---|---|---|---|
| [**US-13**](claude-code/installer/cc-installer-admin-e2e-windows.md) 2026/09 | Claude Code **Windows installer file** — the admin builds one installer; an employee runs it and types two commands (today every PC needs Python, the repository and PATH set up by hand) | Optional · recommended if you have Windows employee PCs · the gateway does not change | Build the installer on a build PC → install on the employee PC → sign in once |
| [**US-12**](ops/8-L-admin-login.md) 2026/09 | **Cognito sign-in** for the admin screen — today anyone who can reach the admin address gets in as an administrator (development login). Sign in with the same Cognito account as employees; only the admin group gets in | Optional · recommended (adds an account check on top of today's address-only protection · needs an https address) · a new install also follows this doc once installation is done | Follow [8-L](ops/8-L-admin-login.md) top to bottom (two rollouts · about 30 min) — register the callback address → turn sign-in on → check → turn the development login off |
| [**US-11**](ops/8-R-pricing.md) 2026/09 | Align model prices with **what AWS actually bills** — calls through the US region group (`us.`) are billed 10% above the global price · Sonnet 5's Sept-1 increase was cancelled · repeat whenever prices change | Required (gateway cost and budgets must match the bill) · included in new installs (§4-2 seeds these prices) | **Done if you ran US-10 via [8-D](ops/8-D-upstream-sync.md)** (step ⑧ is this) · later, when prices change: edit the price file `update-scripts/pricing.tsv`, then apply it with the `08` script (takes effect in 5 min) |
| [**US-10**](ops/8-D-upstream-sync.md) 2026/09 | Bring the gateway to the latest code (a large update: DB structure changes and all 6 services replaced) — 6 outage/error fixes (a health-check misjudgment dropping every pod at once · thinking requests failing · web-search loop errors · double budget charge · every request failing when Claude Code's advisor is on · first request after a long idle failing) · web-search cost caps · web-search improvements (added Sept 19 — search record kept · works alongside app tools · works by default with no settings) | Required (fixes outages) · included in new installs (installing now gives you this code) | Follow [8-D](ops/8-D-upstream-sync.md) top to bottom (about 1.5 h, at a quiet hour) — checks → DB backup → build the new version → deploy → prices → verify · finished before Sept 19? only the Sept 19 additions in [updates.en.md](updates.en.md) US-10 |
| [**US-09**](cowork/installer/cowork-installer-admin-e2e-windows.md) 2026/08 | Cowork Windows installer file — the admin builds one installer; running it on an employee PC finishes the setup (replaces typing settings by hand) | Optional · recommended if you use Cowork on Windows · the gateway does not change | Follow the doc — build the installer on a build PC → install on employee PCs → sign in once |
Earlier (`US-01` initial install) and the why · pitfalls per item → [updates.en.md](updates.en.md)

---

## 3. Applying updates (on the deployment EC2)

**① Bring the repository up to date** — a rebased branch, so not `git pull` but the block below. `values-*.yaml` exists only on this EC2, so the backup · restore is the point (confirm `values restored OK`). `origin` in `git remote -v` must be `gonsoomoon-ml/…` (if it is aws-samples, `set-url`). The prod stack (`US-08`) follows the same steps on the **prod account's deployment EC2** with `V=…/values-eks-fargate-prod.yaml` — `status.sh` does not judge US-08~11 (US-08 is a separate stack, US-09 is PC-side, US-10·11 are judged by `14-postdeploy-check.sh`).

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

**③ Only the missing rows**, via the doc column of the table in §2. Detailed procedure · pitfalls · rollback: [ops/8-U-update.md](ops/8-U-update.md). **`US-10`·`US-11`** (bulk upstream sync — code · schema · prices together) are applied in one procedure, [ops/8-D-upstream-sync.md](ops/8-D-upstream-sync.md) — prod: section ⑩ of the same doc.

---

What the system does (auth · budget · rate limit · inference · accounting) and the diagram → [architecture.md](architecture.md) "전체 그림" · production diagram [8-P §1](ops/8-P-prod.md) · requirements [prd.md](prd.md) · operations [operations.md](operations.md) (all Korean)
