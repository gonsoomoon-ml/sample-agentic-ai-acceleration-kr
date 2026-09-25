# AWSome AI Gateway — Global Edition

[한국어](README.md) · **English**

**An LLM gateway that connects in-house Claude Code · Cowork to Amazon Bedrock** — the one-time installation and every update that follows, in one place.
What sets this edition apart: a region outside Korea · direct to Bedrock (not Mantle) · public https entry · English UI. The "US" in update IDs `US-NN` is the **track name** from the first deployment region (us-west-2) — numbering continues even if you change region.

> Synced with the Korean version through `US-09` (2026-08-29). **The linked procedure documents are Korean-only** (install guide, runbooks, update scripts) — this page tells you *what changed* and *whether this deployment has it*; the runbooks are for the operator who performs the change.

**What you want to do**
- **Install for the first time** — POC: [install-overview.md](install-overview.md) (scope · flow, 10 min) → [install-guide.md](install-guide.md) (run §1–§6-0) · production (separate prod account): [ops/8-P-prod.md](ops/8-P-prod.md) drives [install-guide.md](install-guide.md) §1–§6 in the prod account — decide which in [1. New-install scope](#1-new-install-scope--what-you-use--poc-or-production) first
- **Already installed — see the update state** — `bash status.sh` on the deployment EC2 → apply only the missing rows of [2. Latest updates](#2-latest-updates) below · `status.sh` lists every `US-NN` on its own line; the ones it does not judge (`US-08`·`09`·`10`·`11`·`14`) get a `--` line saying where to check · check `US-10`·`US-11` with `bash 14-postdeploy-check.sh` (DB schema number · prices match)
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

**How each `US-NN` is handled in a new install** — details in [install-overview.md §1](install-overview.md#1-신규-설치와-us-nn) (Korean)

- **Already included (nothing to apply)**: `US-03`·`04`·`05`·`10`·`11`·`13`
- **Done separately after installation**: `US-12` (admin screen Cognito sign-in) — effectively required for production (`US-08`)
- **Pick one of two**: set up employee PCs by hand ↔ installer file (`US-14` Claude Code · `US-09` Cowork)
- **Optional for a POC**: `US-06` (https) · `US-07` (admin internal) · `US-02` is for existing deployments only

---

## 2. Latest updates

**Newest 5 only** — full history (US-01~) and the why · pitfalls per item: [updates.en.md](updates.en.md). `US-NN` is a fixed ID unaffected by rebases. **Check the current state first with [3. Applying updates](#3-applying-updates-on-the-deployment-ec2).**

| ID (doc) | What | Grade · installing new | Already installed — how to apply |
|---|---|---|---|
| [**US-14**](claude-code/installer/cc-installer-admin-e2e-windows.md) 2026/09 | Claude Code **Windows installer file** — the admin builds one installer; an employee runs it and types two commands (today every PC needs Python, the repository and PATH set up by hand) | Optional · recommended if you have Windows employee PCs · `US-01` §6-3 (manual setup) also works · the gateway does not change | Build the installer from the `feat/cc-installer-import` branch → install on the employee PC → sign in once |
| [**US-13**](ops/8-M-models.md) 2026/09 | **Add the Opus 5.5 model** — same context and features as Opus 5 at a 20% lower rate. Register the alias `claude-opus-5-5` and add it to the chain that steps down to Sonnet 5 during an outage | Recommended · the default model stays Sonnet 5 · **a new install gets it from §4-2** (this procedure is for existing ones) | Follow [8-M](ops/8-M-models.md) — alias, model id and rates in `config.env` → `02-add-opus5-model.sh --apply` → `04-verify.sh` after 5 minutes → register the fallback chain and restart gateway-proxy |
| [**US-12**](ops/8-L-admin-login.md) 2026/09 | **Cognito sign-in** for the admin screen — today anyone who can reach the admin address gets in as an administrator (development login). Sign in with the same Cognito account as employees; only the admin group gets in | Optional · recommended (adds an account check on top of today's address-only protection · needs an https address) · **effectively required for production (`US-08`)** · a new install also follows this doc once installation is done | Follow [8-L](ops/8-L-admin-login.md) top to bottom (new admin API version included · two rollouts · about 40 min) — register the callback address → new admin API version and turn sign-in on → check → turn the development login off |
| [**US-11**](ops/8-R-pricing.md) 2026/09 | Align model prices with **what AWS actually bills** — calls through the US region group (`us.`) are billed 10% above the global price · Sonnet 5's Sept-1 increase was cancelled · repeat whenever prices change | Required (gateway cost and budgets must match the bill) · included in new installs (§4-2 seeds these prices) | **Done if you ran US-10 via [8-D](ops/8-D-upstream-sync.md)** (step ⑧ is this) · later, when prices change: edit the price file `update-scripts/pricing.tsv`, then apply it with the `08` script (takes effect in 5 min) |
| [**US-10**](ops/8-D-upstream-sync.md) 2026/09 | Bring the gateway to the latest code (a large update: DB structure changes and all 6 services replaced) — 6 outage/error fixes (a health-check misjudgment dropping every pod at once · thinking requests failing · web-search loop errors · double budget charge · every request failing when Claude Code's advisor is on · first request after a long idle failing) · web-search cost caps · web-search improvements (added Sept 19 — search record kept · works alongside app tools · works by default with no settings) | Required (fixes outages) · included in new installs (installing now gives you this code) | Follow [8-D](ops/8-D-upstream-sync.md) top to bottom (about 1.5 h, at a quiet hour) — checks → DB backup → build the new version → deploy → prices → verify · finished before Sept 19? only the Sept 19 additions in [updates.en.md](updates.en.md) US-10 |
Earlier (`US-01` initial install) and the why · pitfalls per item → [updates.en.md](updates.en.md)

---

## 3. Applying updates (on the deployment EC2)

**① Bring the repository up to date** — a rebased branch, so not `git pull` but the block below. `values-*.yaml` exists only on this EC2, so the backup · restore is the point (confirm `values restored OK`). `origin` in `git remote -v` must be `gonsoomoon-ml/…` (if it is aws-samples, `set-url`). The prod stack (`US-08`) follows the same steps on the **prod account's deployment EC2** with `V=…/values-eks-fargate-prod.yaml` — `status.sh` does not judge US-08~11·14 and instead shows a `--` line saying where to check (US-08 is a separate stack, US-09·14 are PC-side, US-10·11 are judged by `14-postdeploy-check.sh`).

```bash
cd ~/awsome-ai-gateway && git remote -v
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
cp $V ~/values.bak && git fetch origin
git reset --hard origin/us/deploy-fixes && cp ~/values.bak $V
cmp -s $V ~/values.bak && echo "values restored OK" || echo "RESTORE FAILED"
```

**② Check the state** — queries the live system (DB rows · endpoints · image · ALB), changes nothing, 1–2 min (throwaway psql pod). Every item from `US-01` gets its own line (output is Korean) — `OK` applied · `!!`/`XX` partial·missing · `--` optional or checked elsewhere (items it does not judge say where to look). Raw evidence: `--verbose`.

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts && bash status.sh
```

**③ Only the missing rows**, via the doc column of the table in §2. Detailed procedure · pitfalls · rollback: [ops/8-U-update.md](ops/8-U-update.md). **`US-10`·`US-11`** (bulk upstream sync — code · schema · prices together) are applied in one procedure, [ops/8-D-upstream-sync.md](ops/8-D-upstream-sync.md) — prod: section ⑩ of the same doc.

---

What the system does (auth · budget · rate limit · inference · accounting) and the diagram → [architecture.md](architecture.md) "전체 그림" · production diagram [8-P §1](ops/8-P-prod.md) · requirements [prd.md](prd.md) · operations [operations.md](operations.md) (all Korean)
