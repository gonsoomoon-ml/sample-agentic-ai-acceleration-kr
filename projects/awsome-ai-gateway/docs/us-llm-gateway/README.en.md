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
- **Clients · models** — Claude Code (Mac · Windows · Linux) · Cowork · **Opus 5.5 · Sonnet 5.5 · Haiku 4.5** — all included in `US-01`; production (`US-08`) includes them too
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
| Claude Code only (**Opus 5.5 · Sonnet 5.5 · Haiku 4.5**) | `US-01` | `US-08` (the same install as `US-01`, in the prod account per 8-P — https · admin internal · VPN included) |
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
| [**US-17**](ops/8-A-automode-server.md) 2026/10 | Let **Bedrock make Claude Code's Auto-mode safety calls** — today the gateway drops the marker that asks for them, so Claude Code sends a separate classifier request for each check (extra cost) and shows a billing notice. Forwarding just two markers to Bedrock removes those requests and the notice, and the one failed first request per session on recent Claude Code | Recommended · if you use Auto mode · included in new installs | Follow [8-A](ops/8-A-automode-server.md) — update the repo → check script (before) → rebuild only the gateway-proxy image → `install-eks.sh` → check script (after) (about 20 min, no inference downtime) |
| [**US-16**](ops/8-M-models.md) 2026/10 | **Make Sonnet 5.5 the default model** — register the next Sonnet after Sonnet 5 (same rates), switch employee PCs' default model to it, then take the previous generation (Opus 5 · Sonnet 5 · Opus 4.8) off the list. Remaining models = Opus 5.5 · Sonnet 5.5 · Haiku 4.5 | Recommended · **a new install gets it from §4-2** (this procedure is for existing ones) | Follow [8-M](ops/8-M-models.md) sections 0 → A → B → C — register (script or admin UI) → check the first call → switch employee PCs' default model and the fallback rule → deactivate the previous generation → confirm with `status.sh` |
| [**US-15**](ops/8-Z-token-ttl.md) 2026/10 | Gateway key (VK) **lifetime 1 hour → 24 hours** — the helper on each employee PC used to fetch a new key from the admin API every hour; now once a day. Removes the delay at each renewal and the admin API load; the sign-in period (7 days), models and budgets are unchanged | Optional · keep 1 hour if your security policy requires a short lifetime · a fresh install sets it in the same one values line | Follow [8-Z](ops/8-Z-token-ttl.md) — one values line `vkTtlHours: 24` → `install-eks.sh` → only the admin API pods restart (no inference interruption, about 10 min) |
| [**US-14**](claude-code/installer/cc-installer-admin-e2e-windows.md) 2026/09 | Claude Code **Windows installer file** — the admin builds one installer; an employee runs it and types two commands (today every PC needs Python, the repository and PATH set up by hand) | Optional · recommended if you have Windows employee PCs · `US-01` §6-3 (manual setup) also works · the gateway does not change | Build the installer from the `feat/cc-installer-import` branch → install on the employee PC → sign in once |
| [**US-13**](ops/8-M-models.md) 2026/09 | **Add the Opus 5.5 model** — same context and features as Opus 5 at a 20% lower rate. Register the alias `claude-opus-5-5` and add it to the chain that steps down to Sonnet 5 during an outage | Recommended · the default model stays Sonnet 5 (switched to Sonnet 5.5 in US-16) · **a new install gets it from §4-2** (this procedure is for existing ones) | Follow [8-M](ops/8-M-models.md) — alias, model id and rates in `config.env` → `02-add-opus5-model.sh --apply` → `04-verify.sh` after 5 minutes → register the fallback chain and restart gateway-proxy |
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
