# US AWSome AI Gateway

**An LLM gateway that connects in-house Claude Code · Cowork to Amazon Bedrock.**
It covers the one-time installation and every update that follows.

[한국어](README.md) · **English**

> **Synced with the Korean version through `US-04` (2026-08-09).**
> If [README.md](README.md) lists a `US-NN` that is missing here, this page is out of date.

> **The linked procedure documents are Korean-only** (install guide, operations runbook,
> update scripts). This page tells you *what changed* and *whether this deployment has it*;
> the runbooks are for the operator who performs the change.

- 🔴 **Clone from the fork** — [`gonsoomoon-ml/sample-agentic-ai-acceleration-kr`](https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr/tree/us/deploy-fixes/projects/awsome-ai-gateway), branch `us/deploy-fixes`
- **upstream** — [`aws-samples/sample-agentic-ai-acceleration-kr`](https://github.com/aws-samples/sample-agentic-ai-acceleration-kr/tree/main/projects/awsome-ai-gateway). US AWSome AI Gateway is the US-specific customization of that original.
- **Region** — this deployment runs on `us-west-2` (infrastructure). Inference uses **US Geo**, so it is distributed across us-east-1/2 · us-west-2.
  - ⚠️ **Changing the region is not a one-parameter edit** — `aws_region`, `azs` and `bedrock_model_arns` (region-scoped ARNs) in `terraform.tfvars` must change together, and `us-west-2` must be substituted throughout the guides (51 occurrences in install-guide alone).
  - ⚠️ **Deploying outside the US (e.g. Europe) requires configuration changes** — switch the inference profile to `eu.anthropic.`*, adjust model IDs and IAM resource ARNs accordingly, and confirm model availability in that region first. The server-side web search connector is **us-east-1 only**, so it becomes a cross-region call.
- **Inference backend** — `bedrock-runtime` with US Geo inference profiles (`us.anthropic.`*). **Not Bedrock Mantle.**
- **Clients** — Claude Code (Mac · Windows · Linux) · Cowork (after `US-02`)
- **Models** — Opus 4.8 · Sonnet 5 · Haiku 4.5 (+ **Opus 5** via `US-02`)

> The fork is **rebased** onto upstream, so commit hashes change — that is why this document counts versions by `US-NN` rather than by hash. For the full agreed scope, see [install-overview.md §0](install-overview.md#0-이번-배포의-범위-확정) (Korean).

---



## 1. Entry points by task


| Task                  | What it involves                                                        | Documents                                                                                                   |
| --------------------- | ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| **New installation**  | Provision infrastructure → deploy apps → routing · web search → connect clients | [install-overview.md](install-overview.md) → [install-guide.md](install-guide.md)                       |
| **Ongoing updates**   | Check what is applied, then apply only what is missing                   | [2. Latest updates](#2-latest-updates) — run `status.sh` first, then apply only the gaps                     |
| **Client rollout**    | Install Claude Code · Cowork on employee machines                        | [client-install.md](client-install.md) · [cowork/cowork-client-install.md](cowork/cowork-client-install.md) |


> ⚠️ **A new installation still needs `US-02`.** The install migration seeds the Cowork routing row with an account that does not exist, so Cowork will not work even after `install-guide.md` completes — see [2. Latest updates](#2-latest-updates).

---



## 2. Latest updates

🔥 New updates are added at the top. `US-NN` is a fixed identifier that survives rebases.

▶ **Check this deployment first** · on the deployment EC2 (1–2 min, changes nothing)

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts && bash status.sh
```

Apply only the items the check reports as missing. How to read the output is in [3. Checking applied status](#3-checking-applied-status).

> Severity — **Required**: must be applied (compliance or essential capability) · **Recommended**: the feature does not work without it · **Optional**: only if requested

- **[2026/08]** `US-04` **Route Bedrock · STS over VPC Endpoints instead of NAT** — **Required** (compliance) · already included in new installs
Bedrock and STS calls stay on PrivateLink inside the VPC instead of traversing the public internet. Existing deployments have no endpoints and therefore still go through NAT, so they must apply this.
→ [operations.md §8-N](operations.md#8-n-bedrock-을-nat-대신-vpc-endpointprivatelink로) (Korean)
- **[2026/08]** `US-03` **Admin UI Korean/English toggle** — **Required** (English support) · already included in new installs
The entire admin console was converted to i18n, so the KO/EN toggle in the header actually translates the screens. Existing deployments must rebuild the admin-ui image.
→ [operations.md §8-U](operations.md#8-u-업데이트-코드-변경-반영) path **A (service code)** — `rebuild-image.sh admin-ui <env>` → `install-eks.sh <env>` (prerequisite: `06-persist-annotations.sh` dry-run) (Korean)
- **[2026/08]** `US-02` **Cowork connectivity + Opus 5 registration** — **Recommended** (required if you use Cowork) · 🔴 **applies to new installs too**
The install migration seeds the Cowork routing row with **an account that does not exist**, so every Cowork request fails with 502 until it is corrected. This update fixes routing, registers Opus 5 and puts HTTPS (CloudFront) in front.
→ [update-scripts execution order](update-scripts/README.md#실행-순서) (Korean)
- **[2026/07]** `US-01` **Initial installation** — baseline
Stands up the gateway on a single account, `us-west-2`, Claude Code, US Geo inference.
→ [install-overview.md](install-overview.md) (Korean)

---



## 3. Checking applied status

`status.sh` determines whether each update in [2. Latest updates](#2-latest-updates) is present in this deployment by **querying the live system**. It judges from actual deployed state rather than code version — DB routing rows, the CloudFront distribution, VPC endpoints, and the running container image.

The script prints Korean. Status markers are `OK` (applied), `!!` (partially applied) and `XX` (not applied).

Sample output — a deployment where only some updates are applied:

```
 US AWSome AI Gateway — 업데이트 적용 상태
 ────────────────────────────────────────────────────────────
 계정 <ACCOUNT_ID> / us-west-2 · release llm-gateway · ns llm-gateway

   OK   US-01  최초 설치 (기준선)
   !!   US-02  Cowork 연결 + Opus 5 등록 — 일부 적용
        routing=invoke · claude-opus-5 ACTIVE · CloudFront 없음
   XX   US-03  Admin UI 한·영 토글 — 미적용
        이미지 tag 1.0.12 (푸시 2026-08-04 03:11 UTC) — i18n 반입 이전 빌드
   XX   US-04  Bedrock·STS VPC Endpoint — 미적용 (필수)
        엔드포인트 없음 → Bedrock·STS 호출이 NAT 경유

 다음 작업 (update-scripts 디렉터리에서 실행)
 ────────────────────────────────────────────────────────────
   bash 03-create-cloudfront.sh        # Cowork 를 사용하는 경우에만 필요
   bash 09-update-admin-ui.sh          # 선행: bash 06-persist-annotations.sh
```

**Conditions and caveats**

- **Runs on the deployment EC2 only.** The database sits in a private VPC and must be reached through that host, and the kubeconfig for cluster access lives there.
- **It does not change configuration.** It is not read-only in the strictest sense, though: to query the database it creates a throwaway psql pod in the cluster and deletes it afterwards. Fargate scheduling makes this take **1–2 minutes** (measured: 1m20s–1m30s).
- For the raw evidence behind each verdict, run `bash status.sh --verbose`.

---



## 4. System overview

The gateway authenticates Claude Code · Cowork requests, applies per-team and per-user budgets and rate limits, and forwards them to Amazon Bedrock. The request path (data plane) and the management functions (control plane) run as separate services, and usage and cost are recorded at request time.

- **Authentication** — Cognito OIDC login issues a virtual key (VK); every request is validated against it
- **Control** — budgets and rate limits are checked atomically per request; requests over the limit are blocked or downgraded to a cheaper model
- **Inference** — forwarded through `bedrock-runtime` US Geo inference profiles (distributed across us-east-1/2 · us-west-2)
- **Accounting** — per-request tokens and cost are recorded and can be reviewed per team and per user in the Admin UI

For the architecture diagram and request flows see [architecture.md](architecture.md); for the agreed scope of this deployment see [install-overview.md §0](install-overview.md#0-이번-배포의-범위-확정) (both Korean).
