# AWSome AI Gateway — Global Edition

[한국어](README.md) · **English**

**An LLM gateway that connects in-house Claude Code · Cowork to Amazon Bedrock** — the one-time installation and every update that follows, in one place.

**Employee PC** (Claude Code · Cowork) → **gateway** (auth · budgets · cost records) → **Amazon Bedrock** · structure: [architecture.md](architecture.md) "전체 그림" (Korean)

How it differs from the [original](../../README.en.md) (built for Seoul): a region outside Korea · every request goes straight to Bedrock in one account (the original splits apps across accounts and backends, some on Bedrock Mantle) · public https entry · English UI. The "US" in update IDs `US-NN` is the **track name** from the first deployment region (us-west-2) — numbering continues even if you change region.

> **The linked procedure documents are Korean-only** (install guide, runbooks, update scripts) — this page tells you *what changed* and *whether this deployment has it*; the runbooks are for the operator who performs the change.

**What you want to do**
- **Install for the first time** — first decide POC or production in [1. New-install scope](#1-new-install-scope--what-you-use--poc-or-production) → POC: [install-overview.md](install-overview.md) (scope · flow, 10 min) → [install-guide.md](install-guide.md) · production: [ops/8-P-prod.md](ops/8-P-prod.md)
- **Already installed — check update status** — on the deployment EC2, follow [3. Applying updates](#3-applying-updates-on-the-deployment-ec2): refresh the repo → `status.sh` → only the missing rows, via the docs in the [2. Latest updates](#2-latest-updates) table
- **Set up employee PCs only** — Claude Code: [client-install.md](client-install.md) (by hand) · Windows installer [US-14](claude-code/installer/cc-installer-admin-e2e-windows.md) · Cowork: [cowork/README.md](cowork/README.md) (installer · by hand; per-OS doc list)

**This deployment**
- 🔴 **Code** — the fork's **`us/deploy-fixes`** branch: https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr/tree/us/deploy-fixes/projects/awsome-ai-gateway (deployment · vendor fixes not yet in upstream [aws-samples](https://github.com/aws-samples/sample-agentic-ai-acceleration-kr); the `forked from aws-samples/…` banner is expected). It is **rebased** onto upstream, so hashes change — versions are counted by **`US-NN`**
- **Region · inference** — infrastructure in `us-west-2` · inference on `bedrock-runtime` **US Geo** profiles (`us.anthropic.*`, spread across us-east-1/2 · us-west-2) · changing region / deploying outside the US: [install-overview §0](install-overview.md#0-이번-배포의-범위-확정) (Korean)
- **Clients · models** — Claude Code (Mac · Windows · Linux) · Cowork · **Opus 5.5 · Sonnet 5.5 · Haiku 5.5 · Haiku 4.5** — same for POC and production

---

## 1. New-install scope — what you use · POC or production

**POC and production are different stacks, not options on one stack** — production is not a change to dev but a **new stack in a separate account with `environment=prod`** (`US-08`).

**① POC vs production — what differs**

| | POC (dev) | Production (prod) |
|---|---|---|
| Account · sizing | one account · `environment=dev` (Aurora ×1 · Valkey ×1 · NAT ×1) | **separate account** · `environment=prod` (Aurora ×2 · Valkey 3 shards × 3 · NAT ×2) |
| Entry point | http ALB + IP allow-list (mode A) | https domain (`US-06`) + both admin ALBs internal (`US-07`, requires a site-to-site VPN) |
| Procedure | `US-01` — [install-guide.md](install-guide.md) §1–§6 | **`US-08` only** — [install-guide.md](install-guide.md) §1–§6 in the prod account, in the order of [ops/8-P-prod.md](ops/8-P-prod.md) |

**② By the apps you use — what to install**

| Your setup | POC (dev) | Production (prod) |
|---|---|---|
| Claude Code only | `US-01` only | `US-08` only |
| Claude Code + **Cowork** | `US-01` + one https entry (`US-06` with a domain, otherwise the CloudFront part of `US-02`) | `US-08` only (https included, so no extra entry) |

**How each `US-NN` is handled in a new install** — details in [install-overview.md §1](install-overview.md#1-신규-설치와-us-nn) (Korean)

- **Mostly included** — any `US-NN` not listed below is already in the install steps; nothing to apply
- **Done separately after installation**: `US-12` (admin screen Cognito sign-in — effectively required for production `US-08`) · `US-15` (24-hour VK lifetime, optional) · the 2 optional features of `US-20` (body logging · alert email)
- **Pick one of two**: set up employee PCs by hand ↔ installer file (`US-14` Claude Code · `US-09` Cowork)
- **Optional for a POC**: `US-06` (https) · `US-07` (admin internal) · for Cowork without a domain, only the CloudFront part of `US-02`

---

## 2. Latest updates

**Newest 5 only** — full history (US-01~) and the why · pitfalls per item: [updates.en.md](updates.en.md). **Check the current state first with [3. Applying updates](#3-applying-updates-on-the-deployment-ec2).**

| ID (doc) | What | Grade · installing new | Already installed — how to apply |
|---|---|---|---|
| [**US-20**](ops/8-D-upstream-sync.md#us-20-로-따라-할-때-달라지는-것) 2026/10 | **Admin console improvements and fixes** — logout · usage trend chart · budget summary · team-leader rights, plus 2 optional features (body logging · alert email) | Recommended · included in new installs (optional features separately) | [8-D](ops/8-D-upstream-sync.md#us-20-로-따라-할-때-달라지는-것) with the "US-20" differences · about 1 hour |
| [**US-19**](ops/8-D-upstream-sync.md#us-19-로-따라-할-때-달라지는-것) 2026/10 | **Add Haiku 5.5** — the latest fast, low-cost Haiku (input price 1/10 of Haiku 4.5) | Recommended · included in new installs | [8-D](ops/8-D-upstream-sync.md#us-19-로-따라-할-때-달라지는-것) with the "US-19" differences · about 1 hour |
| [**US-18**](ops/8-D-upstream-sync.md#us-18-로-따라-할-때-달라지는-것) 2026/10 | **Budget, cost and permission fixes** — 7 fixes incl. budget alerts, team-budget access, cost records (Sonnet 5.5 cache price) | Required · included in new installs | [8-D](ops/8-D-upstream-sync.md#us-18-로-따라-할-때-달라지는-것) with the "US-18" differences · about 1 hour |
| [**US-17**](ops/8-A-automode-server.md) 2026/10 | **Bedrock makes Claude Code's Auto-mode safety calls** — the separate check request and billing notice go away | Part of US-18 · included in new installs | **Nothing separate** — comes with US-18 · check only with [8-A](ops/8-A-automode-server.md) |
| [**US-16**](ops/8-M-models.md) 2026/10 | **Make Sonnet 5.5 the default model** — the previous generation (Opus 5 · Sonnet 5 · Opus 4.8) leaves the list | Recommended · included in new installs | [8-M](ops/8-M-models.md) steps "0" → "C" |

---

## 3. Applying updates (on the deployment EC2)

**① Bring the repository up to date** — a rebased branch, so not `git pull` but the commands below.
- `origin` in `git remote -v` must be `gonsoomoon-ml/…` (if it is aws-samples, `set-url`)
- `values-*.yaml` exists only on this EC2, so the backup · restore is the point — confirm `values restored OK`
- prod (`US-08`): the same steps on the **prod account's deployment EC2** with `V=…/values-eks-fargate-prod.yaml`

▶ **Run** · deployment EC2

```bash
cd ~/awsome-ai-gateway && git remote -v
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
cp $V ~/values.bak && git fetch origin
git reset --hard origin/us/deploy-fixes && cp ~/values.bak $V
cmp -s $V ~/values.bak && echo "values restored OK" || echo "RESTORE FAILED"
```

**② Check the state** — queries the live system (DB rows · endpoints · image · ALB), changes nothing, 1–2 min (throwaway psql pod). Every item from `US-01` gets its own line (output is Korean) — `OK` applied · `!!`/`XX` partial·missing · `--` optional or checked elsewhere (items it does not judge say where to look). Raw evidence: `--verbose`.

▶ **Run** · deployment EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts && bash status.sh
```

**③ Only the missing rows**, via the doc column of the table in §2. Every update whose "how to apply" points to **8-D** (code, DB and prices change together) uses the single [ops/8-D-upstream-sync.md](ops/8-D-upstream-sync.md) procedure — read only your update's differences in (0) at the top; from (1) on it is the same. prod: section (10) of the same doc. Updates that change one service, and rollback in general: [ops/8-U-update.md](ops/8-U-update.md).

---

What the system does (auth · budget · rate limit · inference · accounting) and the diagram → [architecture.md](architecture.md) "전체 그림" · production diagram [8-P §1](ops/8-P-prod.md) · requirements [prd.md](prd.md) · operations [operations.md](operations.md) (all Korean)
