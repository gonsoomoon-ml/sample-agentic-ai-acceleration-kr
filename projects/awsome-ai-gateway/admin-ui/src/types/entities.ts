// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

import type {
  AlertLevel,
  BudgetScope,
  GroupByType,
  KeyStatus,
  OrgNodeType,
  PeriodType,
  RateLimitScope,
  UserRole,
} from './enums';

// ─── Auth / Session ───────────────────────────────────────────────────────────

export interface AdminSession {
  user_id: string;
  email: string;
  display_name: string;
  role: UserRole;
  team_id: string | null;
  department_id: string | null;
  issued_at: string; // ISO 8601
  expires_at: string; // ISO 8601
}

// ─── Virtual Keys ─────────────────────────────────────────────────────────────

export interface VirtualKeyListItem {
  key_id: string;
  key_prefix: string;
  user_id: string;
  user_email: string | null;
  status: KeyStatus;
  created_at: string; // ISO 8601
  expires_at: string | null; // ISO 8601
  last_used_at: string | null; // ISO 8601
}

export interface VirtualKeyDetail extends VirtualKeyListItem {
  /** Full key value — only returned immediately after creation */
  key_value: string;
}

// ─── Budgets ──────────────────────────────────────────────────────────────────

export interface BudgetSummaryItem {
  target_id: string;
  target_type: BudgetScope;
  target_name: string;
  team_id?: string | null;
  is_active?: boolean;
  limit: number | null;  // null = 개인 예산 미설정 (팀 예산 적용)
  used: number;
  remaining: number | null;
  usage_pct: number | null;
  alert_level: AlertLevel;
}

// ─── Models ───────────────────────────────────────────────────────────────────

export interface ModelListItem {
  alias: string;
  provider: string;
  model_id: string;
  endpoint_url: string | null;
  is_active: boolean;
  input_price_per_1k: number;
  output_price_per_1k: number;
  cache_creation_5m_price_per_1k: number;
  cache_creation_1h_price_per_1k: number;
  cache_read_price_per_1k: number;
  max_tokens: number;
  context_window: number;
  description: string | null;
  display_name: string | null;
}

// ─── Team Model Access ───────────────────────────────────────────────────────

export interface TeamAllowedModels {
  team_id: string;
  model_aliases: string[];
}

// ─── Auto-Downgrade ──────────────────────────────────────────────────────────

export interface DowngradeRule {
  id: string;
  from_model_alias: string;
  to_model_alias: string;
  threshold_pct: number;
  is_active: boolean;
  created_at: string;
}

export interface AutoDowngradeConfig {
  scope: string;
  scope_id: string;
  enabled: boolean;
  rules: DowngradeRule[];
}

// ─── Rate Limits ──────────────────────────────────────────────────────────────

export interface RateLimitConfig {
  target_id: string;
  scope: RateLimitScope;
  rpm: number | null; // requests per minute
  tpm: number | null; // tokens per minute
  cpm: number | null; // cost per minute (USD)
  cph: number | null; // cost per hour (USD)
}

export interface RateLimitTreeNode {
  id: string;
  label: string;
  scope: RateLimitScope;
  is_active?: boolean;
  config: RateLimitConfig | null;
  children: RateLimitTreeNode[];
  inherited_from: string | null; // node id of config source when inherited
}

// ─── Organisation Tree ────────────────────────────────────────────────────────

/**
 * 조직 트리 검색창의 사용자 결과 1건.
 *
 * `UserResponse` 와 별개인 이유: 검색은 트리에서 노드를 찾아 선택하는 용도라
 * created_at/is_active 가 불필요하고, 반대로 `team_id` 가 **반드시** 필요하다 —
 * 팀 멤버는 트리에서 lazy-load 되므로 조상 경로를 펼치려면 팀 id 를 알아야 한다.
 */
export interface UserSearchItem {
  id: string;
  email: string;
  display_name: string;
  role: UserRole;
  team_id: string | null;
  team_name: string | null;
}

export interface OrgNodeMeta {
  /** 항상 **사람 수**. 노드 타입과 무관하다(admin-api OrgNodeMeta 주석 참조). */
  member_count: number | null;
  /** 하위 팀 수. DEPARTMENT / ORGANIZATION 만 채워지고 TEAM·USER 는 null. */
  team_count: number | null;
  leader_name: string | null;
  leader_user_id: string | null;
  email: string | null;
  role: UserRole | null;
  team_name: string | null;
  // budgets / rate-limits 트리에서 동일한 OrgTree 컴포넌트를 재사용하면서
  // 우측 패널에 보여줄 데이터를 메타로 붙인다.
  budget?: {
    limit: number | null;
    used: number;
    remaining: number | null;
    usage_pct: number | null;
    alert_level: AlertLevel;
  };
  rateLimit?: {
    config: RateLimitConfig | null;
    scope: RateLimitScope;
    inherited_from: string | null;
    is_active: boolean;
  };
}

export interface OrgTreeNode {
  id: string;
  name: string;
  type: OrgNodeType;
  children: OrgTreeNode[];
  meta: OrgNodeMeta;
}

// ─── CLI Downloads ────────────────────────────────────────────────────────────

export interface CLIDownloadItem {
  os: string;
  arch: string;
  filename: string;
  download_url: string;
  version: string;
  file_size_bytes: number;
  checksum_sha256: string;
}

// ─── Dashboard KPIs ───────────────────────────────────────────────────────────

export interface DashboardKPI {
  total_usage_usd: number;
  active_keys: number;
  active_models: number;
  budget_utilization_percent: number;
}

// ─── ROI / Analytics ─────────────────────────────────────────────────────────

export interface ROIMetrics {
  cost_per_line: number;
  cost_per_commit: number;
  productivity_gain_percent: number;
  roi_ratio: number;
}

export interface TrendDataPoint {
  date: string; // ISO 8601 date (YYYY-MM-DD)
  cost_usd: number;
}

export interface ModelBreakdown {
  model_alias: string;
  cost_usd: number;
  token_count: number;
  request_count: number;
}

export interface TeamBreakdown {
  team_id: string;
  team_name: string;
  cost_usd: number;
  token_count: number;
  request_count: number;
}

export interface CostSummary {
  total_cost_usd: number;
  period: PeriodType;
  start_date: string; // ISO 8601
  end_date: string; // ISO 8601
  trend: TrendDataPoint[];
  by_model: ModelBreakdown[];
  by_team: TeamBreakdown[];
}

export interface ProductivitySummary {
  period: PeriodType;
  start_date: string;
  end_date: string;
  roi: ROIMetrics;
  commits: number;
  lines_generated: number;
  active_developers: number;
}

export interface ROIAnalyticsResponse {
  cost: CostSummary;
  productivity: ProductivitySummary;
  group_by: GroupByType;
}

// ─── Budget Allocation ────────────────────────────────────────────────────────

export interface AllocationEntry {
  target_id: string;
  target_name: string;
  target_type: BudgetScope;
  /** USER 행의 계정 역할(ADMIN|TEAM_LEADER|USER). TEAM 행은 null/부재. */
  target_role?: string | null;
  allocated_usd: number;
  used_usd: number;
  remaining_usd: number;
  alert_level: AlertLevel;
}

export interface TeamBudgetAllocation {
  team_id: string;
  team_name: string;
  total_budget_usd: number;
  entries: AllocationEntry[];
}

// ─── UI State ─────────────────────────────────────────────────────────────────

export interface ToastNotification {
  type: 'success' | 'error' | 'warning' | 'info';
  message: string;
  auto_dismiss_ms: number | null;
}

export interface FormFieldError {
  field: string;
  message: string;
}


// ─── Effective Policy (GET /admin/users/{id}/effective-policy) ────────────────

export interface EffectivePolicyCell {
  client: string;
  model_alias: string;
  allowed: boolean;
  blocked_by: string[]; // "user_app" | "user_model" | "model_app"
}

export interface EffectiveBudgetEntry {
  scope: string;
  client: string | null;
  max_budget_usd: string;
  used_usd: string | null;
  policy: string;
}

export interface EffectiveRateLimitEntry {
  scope: string;
  model_alias: string | null;
  rpm_limit: number | null;
  tpm_limit: number | null;
  cpm_limit_usd: string | null;
  cph_limit_usd: string | null;
}

export interface EffectiveDowngradeRule {
  scope: string;
  threshold_pct: number;
  from_model_alias: string;
  to_model_alias: string;
}

export interface EffectivePolicy {
  user_id: string;
  email: string | null;
  team_id: string | null;
  team_name: string | null;
  allowed_clients: string[] | null;
  allowed_clients_source: 'user' | 'team' | 'organization' | 'none';
  allowed_models: string[] | null;
  allowed_models_source: 'user' | 'team' | 'none';
  web_search: Record<string, boolean>;
  cells: EffectivePolicyCell[];
  budgets: EffectiveBudgetEntry[];
  rate_limits: EffectiveRateLimitEntry[];
  downgrade_rules: EffectiveDowngradeRule[];
}
