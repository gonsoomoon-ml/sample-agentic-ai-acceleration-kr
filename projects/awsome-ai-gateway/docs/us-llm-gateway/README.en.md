# AWSome AI Gateway — Global Edition

[한국어](README.md) · **English**

**An LLM gateway that connects in-house Claude Code · Cowork to Amazon Bedrock** — the one-time installation and every update that follows, in one place.
What sets this edition apart: a region outside Korea · direct to Bedrock (not Mantle) · public https entry · English UI. The "US" in update IDs `US-NN` is the **track name** from the first deployment region (us-west-2) — numbering continues even if you change region.

> Synced with the Korean version through `US-07` (2026-08-20). **The linked procedure documents are Korean-only** (install guide, runbooks, update scripts) — this page tells you *what changed* and *whether this deployment has it*; the runbooks are for the operator who performs the change.

**What you want to do**
- **Install for the first time** — [install-overview.md](install-overview.md) (scope · flow, 10 min) → [install-guide.md](install-guide.md) (run §1–§6-0)
- **Already installed — see the update state** — `bash status.sh` on the deployment EC2 → apply only the missing rows of [2. Latest updates](#2-latest-updates) below
- **Set up employee PCs only** — [client-install.md](client-install.md) (Claude Code) · [cowork/…windows.md](cowork/cowork-client-install-windows.md) · [cowork/…windows-auto.md](cowork/cowork-client-install-windows-auto.md) (installer) · [cowork/…macos.md](cowork/cowork-client-install-macos.md)

**This deployment**
- 🔴 **Code** — the fork's **`us/deploy-fixes`** branch: https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr/tree/us/deploy-fixes/projects/awsome-ai-gateway (deployment · vendor fixes not yet in upstream [aws-samples](https://github.com/aws-samples/sample-agentic-ai-acceleration-kr); the `forked from aws-samples/…` banner is expected). It is **rebased** onto upstream, so hashes change — versions are counted by **`US-NN`**
- **Region** — `us-west-2` (infrastructure) · inference on **US Geo** (`us.anthropic.*`, spread across us-east-1/2 · us-west-2) · changing region / deploying outside the US: [install-overview §0](install-overview.md#0-이번-배포의-범위-확정) (Korean)
- **Inference backend** — `bedrock-runtime` + US Geo inference profiles (not Mantle)
- **Clients · models** — Claude Code (Mac · Windows · Linux) · Cowork (after `US-02`) · Opus 4.8 · Sonnet 5 · Haiku 4.5 (+ Opus 5 = `US-02`)
- **Entry point** — http ALB + IP allow-list (mode A) by default. With a domain, an **https domain** (`US-06`, ACM) — **strongly recommended for production**

---

## 1. New-install scope — what you use · POC or production

| Your setup | POC (no domain · http ALB + IP allow-list) | Production (domain · https · with a site-to-site VPN + `US-07`) |
|---|---|---|
| Claude Code only (Opus 4.8 · Sonnet 5 · Haiku 4.5) | `US-01` | `US-01` + `US-06` |
| Claude Code only + **Opus 5** | `US-01` + step `02` of `US-02` (model registration) | `US-01` + step `02` of `US-02` + `US-06` |
| Claude Code + **Cowork** | `US-01` + all of `US-02` (`01` routing · `02` model · `03` CloudFront) | `US-01` + steps `01`·`02` of `US-02` + `US-06` (`03` not needed) |

> `US-06` (ALB HTTPS) = https via your domain + ACM — **strongly recommended for production**. Cowork requires https: CloudFront (`03`) without a domain, US-06 with one — never both. `US-03·04·05` are included in new installs (required).
> `US-07` (admin ALBs internal) = the **final posture for production with a site-to-site VPN** (optional) — new installs include it during `US-01` by uncommenting the values **at §3-6**; existing deployments follow the [switch runbook](ops/8-I-admin-internal.md). Do not apply without the VPN.
> ⚠️ **A new install still needs `US-02`** — the migration seeds the Cowork routing row with a non-existent account, so left as is every Cowork request is a 502. Claude Code only + seed models → US-02 can be skipped.

---

## 2. Latest updates

**Newest 5 only** — full history (US-01~) and the why · pitfalls per item: [updates.en.md](updates.en.md). `US-NN` is a fixed ID unaffected by rebases. **Check the current state first with [3. Applying updates](#3-applying-updates-on-the-deployment-ec2).**

| ID (doc) | What | Grade · new installs | Existing deployments do |
|---|---|---|---|
| [**US-07**](ops/8-I-admin-internal.md) 2026/08 | Customer final architecture — both admin ALBs internal (private subnets) | Optional · requires site-to-site VPN · new installs: at §3-6 via values | uncomment 2 values blocks → helm (ALB recreation) → swap admin SG · CNAMEs |
| [**US-06**](ops/8-H-alb-https.md) 2026/08 | ALB HTTPS — custom domain + ACM | Optional · **strongly recommended for production** · new installs: at §3-6 | get a domain → switch → update 2 client URLs (30 min) |
| [**US-05**](ops/8-E-eks-upgrade.md) 2026/08 | EKS 1.31 → 1.34 | Required (support expiry · cost) · included in new installs | apply one minor at a time ×3 + restart all ns |
| [**US-04**](ops/8-N-vpc-endpoint.md) 2026/08 | Bedrock · STS over VPC Endpoints | Required (compliance) · included in new installs | apply endpoints → restart gateway-proxy |
| [**US-03**](ops/8-U-update.md) 2026/08 | Admin UI KO/EN toggle | Required (English support) · included in new installs | rebuild admin-ui image → install-eks |
Earlier (`US-01` initial install) and the why · pitfalls per item → [updates.en.md](updates.en.md)

---

## 3. Applying updates (on the deployment EC2)

**① Bring the repository up to date** — a rebased branch, so not `git pull` but the block below. `values-*.yaml` exists only on this EC2, so the backup · restore is the point (confirm `values restored OK`). `origin` in `git remote -v` must be `gonsoomoon-ml/…` (if it is aws-samples, `set-url`).

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

What the system does (auth · budget · rate limit · inference · accounting) and the diagram → [architecture.md](architecture.md) "전체 그림" · requirements [prd.md](prd.md) · operations [operations.md](operations.md) (all Korean)
