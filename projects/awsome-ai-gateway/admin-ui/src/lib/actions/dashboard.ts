'use server';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.


import { adminAPI } from '@/lib/api-client';
import { withRetry } from '@/lib/utils/retry';
import { currentCalendarMonth, monthsAgo } from '@/lib/utils/period';

export interface DashboardSummary {
  period: string;
  total_requests: number;
  total_tokens: number;
  total_cost_usd: number;
  active_users: number;
  cost_per_user_usd: number;
}

export interface ModelShareItem {
  model_alias: string;
  display_name?: string | null;
  cost_usd: number;
  share_pct: number;
}

export interface ModelShareResponse {
  period: string;
  team_id: string;
  total_cost_usd: number;
  models: ModelShareItem[];
}

export interface ClientShareItem {
  client: string;        // 'claude-code' | 'cowork' | 'codex' | 'other'
  cost_usd: number;
  share_pct: number;
  call_count: number;
  web_search_count: number;   // server-side AgentCore web searches (attribution metric)
}

export interface ClientShareResponse {
  period: string;
  total_cost_usd: number;
  clients: ClientShareItem[];
}

export interface TeamOption {
  id: string;
  name: string;
  // 팀명이 부서 간 중복될 수 있어(예: 여러 부서에 "Developers" 팀), 드롭다운
  // 표시에 부서명을 병기해 구분한다.
  department_name: string | null;
}

export interface AvailablePeriods {
  periods: string[]; // newest-first, e.g. ['2026-06','2026-05', ...]
  latest: string; // periods[0], or current calendar month if empty
}

/**
 * 사용량 데이터가 있는 월 목록 + 기본 월(latest).
 * 백엔드 /admin/dashboard/periods 가 데이터 있는 월만 최신순 반환.
 * 빈 DB / 엔드포인트 미배포(404) 시 현재 달력월로 graceful fallback.
 */
export async function fetchAvailablePeriods(): Promise<AvailablePeriods> {
  // KST 기준 — 백엔드 집계 버킷과 일치시킨다. pod 로컬(UTC)로 계산하면 매월 1일
  // 00:00~09:00 KST 사이에 "이번 달" 버튼이 지난 달을 가리켰다.
  const thisMonth = currentCalendarMonth();
  const lastMonth = monthsAgo(1);
  try {
    const res = await withRetry(() =>
      adminAPI.get<{ periods: string[] }>('/admin/dashboard/periods'),
    );
    const withData = (res.periods ?? []).filter(Boolean);
    // 이번 달·지난 달은 데이터가 0이어도 선택기 고정 버튼이라 항상 포함.
    // 그 외 데이터 있는 과거 월은 드롭다운에. dedup 후 최신순.
    const periods = Array.from(new Set([thisMonth, lastMonth, ...withData])).sort().reverse();
    return {
      // 기본(latest)은 "데이터 있는 가장 최근 월" — 첫 화면이 비지 않도록.
      // 데이터가 전혀 없으면 이번 달.
      latest: withData.slice().sort().reverse()[0] ?? thisMonth,
      periods,
    };
  } catch (err) {
    // 백엔드 미배포(404)/장애를 빈 DB 와 구분해 로그로 남김 — 둘 다 graceful
    // fallback(이번/지난 달)이지만, 침묵으로 outage 가 가려지지 않도록 기록.
    console.error('[fetchAvailablePeriods] /admin/dashboard/periods 실패 — 현재월로 fallback:', err);
    return { periods: [thisMonth, lastMonth], latest: thisMonth };
  }
}

/**
 * 대시보드 상단 KPI 카드 일괄 응답.
 *
 * ⚠️ `null` 은 "값이 0" 이 아니라 **"알 수 없음"** 이다. 반드시 '—' 로 렌더할 것 —
 * 0 으로 접으면 "활성 키 0개"·"예산 미사용" 같은 거짓 사실을 화면에 쓰게 된다.
 * (`budget_utilization_pct` 는 한도 합계가 0 일 때 null 이다.)
 */
export interface DashboardKPI {
  period: string;
  total_requests: number;
  total_tokens: number;
  total_cost_usd: number;
  active_users: number;
  cost_per_user_usd: number;
  budget_used_usd: number;
  budget_limit_usd: number;
  budget_utilization_pct: number | null;
  active_keys: number;
  active_models: number;
}

/**
 * KPI 카드용 단일 호출.
 *
 * 예전에는 이 화면 하나를 그리려고 `/admin/budgets/summary`, `/admin/keys/count`,
 * `/admin/models`, `/admin/dashboard/summary` 4개를 동시에 불렀다. 그중
 * `/admin/budgets/summary` 가 예산 config 하나당 Redis GET + SQL SUM 을 순차로 돌려
 * 사용자 수에 비례해 느려지는 병목이었다. 카드에 필요한 건 합계 몇 개뿐이므로
 * 백엔드에서 SQL 집계 한 번으로 낸다.
 */
export async function fetchDashboardKPI(period?: string, client?: string): Promise<DashboardKPI> {
  const params: Record<string, string> = {};
  if (period) params.period = period;
  if (client && client !== 'all') params.client = client;
  return withRetry(() =>
    adminAPI.get<DashboardKPI>(
      '/admin/dashboard/kpi',
      Object.keys(params).length ? params : undefined,
    ),
  );
}

export async function fetchDashboardSummary(period?: string, client?: string): Promise<DashboardSummary> {
  const params: Record<string, string> = {};
  if (period) params.period = period;
  if (client && client !== 'all') params.client = client;
  return withRetry(() =>
    adminAPI.get<DashboardSummary>(
      '/admin/dashboard/summary',
      Object.keys(params).length ? params : undefined,
    ),
  );
}

export async function fetchModelShare(
  period?: string,
  teamId?: string,
  client?: string,
): Promise<ModelShareResponse> {
  const params: Record<string, string> = {};
  if (period) params.period = period;
  if (teamId) params.team_id = teamId;
  if (client && client !== 'all') params.client = client;
  return withRetry(() =>
    adminAPI.get<ModelShareResponse>(
      '/admin/dashboard/model-share',
      Object.keys(params).length ? params : undefined,
    ),
  );
}

export async function fetchClientShare(period?: string): Promise<ClientShareResponse> {
  return withRetry(() =>
    adminAPI.get<ClientShareResponse>(
      '/admin/dashboard/client-share',
      period ? { period } : undefined,
    ),
  );
}

export async function fetchTeamOptions(): Promise<TeamOption[]> {
  const res = await withRetry(() =>
    adminAPI.get<{ items: Array<{ id: string; name: string; department_name: string | null }> }>(
      '/admin/users/teams',
    ),
  );
  return res.items.map((t) => ({ id: t.id, name: t.name, department_name: t.department_name ?? null }));
}

// ── Analytics (cost trend + breakdowns) — /admin/analytics ──
// trends: 일별 비용/요청, by_team/by_model: 분해. group_by 로 user 분해도 가능.

export interface TrendItem {
  date: string;
  cost_usd: number;
  requests: number;
}

export interface TeamBreakdown {
  team: string;
  team_id: string;
  cost_usd: number;
  active_users: number;
}

export interface TeamTrend {
  team: string;
  team_id: string;
  dept_name?: string | null;
  points: TrendItem[];
}

export interface AnalyticsResponse {
  period: string;
  currency: string;
  by_team: TeamBreakdown[];
  trends: TrendItem[];
  trends_by_team?: TeamTrend[];
}

/** 비용 추이(trends) + 팀별 분해(by_team) 를 한 번에. group_by=team. */
export async function fetchAnalytics(
  period: string,
  groupBy: 'team' | 'user' | 'model' = 'team',
  client?: string,
): Promise<AnalyticsResponse> {
  const params: Record<string, string> = { period, group_by: groupBy, scope: 'all' };
  if (client && client !== 'all') params.client = client;
  return withRetry(() => adminAPI.get<AnalyticsResponse>('/admin/analytics', params));
}

// ── Budget summary (Top 팀/사용자 by 비용) — /admin/budgets/summary ──

export interface BudgetSummaryItem {
  target_type: string; // 'team' | 'user'
  target_id: string;
  target_name: string | null;
  team_id: string | null;
  used_usd: string;
  limit_usd: string | null;
  usage_pct: string | null;
}

export async function fetchBudgetSummary(period: string): Promise<BudgetSummaryItem[]> {
  const res = await withRetry(() =>
    adminAPI.get<{ summary: BudgetSummaryItem[] }>('/admin/budgets/summary', { period }),
  );
  return res.summary ?? [];
}

// 실제 비용 기준 상위 사용자(§60.8) — usage_logs SUCCESS+KST 집계. 기존 'Top 사용자
// by 비용' 위젯이 budgets/summary(예산설정자만)를 써 헤비유저를 누락하던 버그 수정.
export interface TopUserItem {
  user_id: string;
  name: string;
  email: string;
  team_id: string | null;
  team_name: string | null; // 부서 접두어 포함(예: "NDS_Developers") — budget_service 와 동일 규칙
  department_name: string | null;
  cost_usd: number;
  call_count: number;
}

export async function fetchTopUsers(period?: string, limit = 5, client?: string): Promise<TopUserItem[]> {
  const params: Record<string, string> = { limit: String(limit) };
  if (period) params.period = period;
  if (client && client !== 'all') params.client = client;
  const res = await withRetry(() =>
    adminAPI.get<{ users: TopUserItem[] }>('/admin/dashboard/top-users', params),
  );
  return res.users ?? [];
}

// 실제 비용 기준 상위 팀(§60.9) — usage_logs.team_id 직접 집계(SUCCESS+KST). top-users 와
// 동형으로, 예산 미설정 팀도 포함(기존 budgets/summary 소스는 예산설정 팀만 누락 위험).
export interface TopTeamItem {
  team_id: string;
  name: string; // 부서 접두어 포함(예: "NDS_Developers") — budget_service 와 동일 규칙
  department_name: string | null;
  cost_usd: number;
  call_count: number;
}

export async function fetchTopTeams(period?: string, limit = 5, client?: string): Promise<TopTeamItem[]> {
  const params: Record<string, string> = { limit: String(limit) };
  if (period) params.period = period;
  if (client && client !== 'all') params.client = client;
  const res = await withRetry(() =>
    adminAPI.get<{ teams: TopTeamItem[] }>('/admin/dashboard/top-teams', params),
  );
  return res.teams ?? [];
}