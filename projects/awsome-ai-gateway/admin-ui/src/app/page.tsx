// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

import { KPICard } from '@/components/common/KPICard';
import { SkeletonCard } from '@/components/common/SkeletonCard';
import { AlertLevel } from '@/types/enums';
import {
  DollarSign,
  Key,
  Cpu,
  BarChart3,
  Activity,
  Coins,
  Users,
  CalendarClock,
} from 'lucide-react';
import { Suspense } from 'react';
import { getTranslations } from 'next-intl/server';
import {
  fetchDashboardKPI,
  fetchModelShare,
  fetchTeamOptions,
  fetchAnalytics,
  fetchBudgetSummary,
  fetchTopUsers,
  fetchTopTeams,
  fetchAvailablePeriods,
  fetchClientShare,
  type ClientShareResponse,
} from '@/lib/actions/dashboard';
import { ModelShareDonutClient } from '@/components/dashboard/ModelShareDonutClient';
import { ClientShareDonutClient } from '@/components/dashboard/ClientShareDonutClient';
import { CostTrendCard } from '@/components/dashboard/CostTrendCard';
import { TopSpendTable, type TopSpendRow } from '@/components/dashboard/TopSpendTable';
import { PeriodSelector } from '@/components/dashboard/PeriodSelector';
import { ClientFilter } from '@/components/dashboard/ClientFilter';
import { kstNowParts } from '@/lib/utils/period';



function calcAlertLevel(utilization: number): (typeof AlertLevel)[keyof typeof AlertLevel] {
  if (utilization >= 95) return AlertLevel.CRITICAL;
  if (utilization >= 80) return AlertLevel.WARNING;
  return AlertLevel.NORMAL;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function fmtUsd2(n: number): string {
  return `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/**
 * 일 평균 소비 + (당월이면) 월말 예상.
 * 실데이터: dashboard summary 의 total_cost_usd 를 경과일로 나눔.
 * - 당월: 오늘까지 경과일로 나눠 일평균 → 그 달 총일수 곱해 월말 예상(선형 추정).
 * - 과거월: 그 달 총일수로 나눔(예상 없음, 이미 확정).
 */
function computeDailyAvg(period: string, totalCost: number): {
  dailyAvg: number;
  projection: number | null;
} {
  const [y, m] = period.split('-').map(Number);
  const daysInMonth = new Date(y, m, 0).getDate();
  // ⚠️ "지금" 은 KST 로 구한다. 분자(summary.total_cost_usd)는 백엔드에서 KST 버킷으로
  //    집계되는데 분모를 pod 의 UTC 시계로 나누면 매일 00:00~09:00 KST 사이에 경과일이
  //    하루 적어 일평균이 과대계상되고, 매월 1일 그 9시간 동안은 isCurrentMonth 가
  //    false 가 되어 월말 예상이 아무 설명 없이 사라진다.
  const kstNow = kstNowParts();
  const isCurrentMonth = y === kstNow.y && m === kstNow.m;
  const elapsedDays = isCurrentMonth ? kstNow.d : daysInMonth;
  const dailyAvg = elapsedDays > 0 ? totalCost / elapsedDays : 0;
  const projection = isCurrentMonth ? dailyAvg * daysInMonth : null;
  return { dailyAvg, projection };
}

async function DashboardKPIs({ period, client }: { period: string; client: string }) {
  const t = await getTranslations('dashboard');

  // 카드 전체가 단일 엔드포인트에서 온다. 예전에는 4개를 Promise.allSettled 로 동시에
  // 불렀고 그중 /admin/budgets/summary 가 예산 config 하나당 Redis GET + SQL SUM 을
  // 순차로 돌아 사용자 수에 비례해 느려지는 병목이었다(수백~수천 명 규모에서 수십 초).
  //
  // ⚠️ 단일 호출은 "한 번 실패하면 카드가 전부 빈다" 는 대가가 있다. 예전 구조는 카드별
  //    degradation 이 가능했다. 그래서 실패를 0 으로 접지 않고 **전부 '—' + fetchFailed**
  //    로 렌더한다 — 부분적으로 그럴듯한 화면보다 "지금 값을 모른다" 가 정확하다.
  const kpi = await fetchDashboardKPI(period, client).catch(() => null);

  const budgetUtilization = kpi?.budget_utilization_pct ?? null;
  // 한도 합계가 0 이면 비율이 정의되지 않는다(백엔드가 null 을 준다). 0% 로 접으면
  // "예산을 하나도 안 썼다" 는 거짓 사실이 되므로 경고 등급도 매기지 않는다.
  const alertLevel = budgetUtilization != null ? calcAlertLevel(budgetUtilization) : undefined;

  const { dailyAvg, projection } = kpi
    ? computeDailyAvg(period, kpi.total_cost_usd)
    : { dailyAvg: 0, projection: null };

  return (
    <div className="space-y-6">
      {/* ── 비용 & 예산 ── */}
      <section className="space-y-3">
        <h2 className="px-1 text-xs font-bold uppercase tracking-[0.12em] text-muted-foreground">
          {t('costAndBudget')}
        </h2>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
          <KPICard
            title={t('usageThisMonth')}
            value={kpi ? fmtUsd2(kpi.total_cost_usd) : '—'}
            icon={<DollarSign size={18} aria-hidden="true" />}
            description={kpi ? t('usageThisMonthDesc') : t('fetchFailed')}
          />
          <KPICard
            title={t('budgetUtilization')}
            value={budgetUtilization != null ? `${budgetUtilization.toFixed(1)}%` : '—'}
            icon={<BarChart3 size={18} aria-hidden="true" />}
            alertLevel={alertLevel}
            description={budgetUtilization != null ? t('budgetUtilizationDesc') : t('fetchFailed')}
          />
          <KPICard
            title={t('avgCostPerUser')}
            value={kpi ? fmtUsd2(kpi.cost_per_user_usd) : '—'}
            icon={<Users size={18} aria-hidden="true" />}
            description={kpi ? t('avgCostPerUserDesc', { count: kpi.active_users }) : t('fetchFailed')}
          />
          <KPICard
            title={t('dailyAvg')}
            value={kpi ? fmtUsd2(dailyAvg) : '—'}
            icon={<CalendarClock size={18} aria-hidden="true" />}
            description={
              projection != null
                ? t('dailyAvgProjection', { amount: fmtUsd2(projection) })
                : t('dailyAvgDesc')
            }
          />
        </div>
      </section>

      {/* ── 사용량 & 시스템 ── */}
      <section className="space-y-3">
        <h2 className="px-1 text-xs font-bold uppercase tracking-[0.12em] text-muted-foreground">
          {t('usageAndSystem')}
        </h2>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
          <KPICard
            title={t('totalRequests')}
            value={kpi ? kpi.total_requests.toLocaleString() : '—'}
            icon={<Activity size={18} aria-hidden="true" />}
            description={t('totalRequestsDesc')}
          />
          <KPICard
            title={t('totalTokens')}
            value={kpi ? formatTokens(kpi.total_tokens) : '—'}
            icon={<Coins size={18} aria-hidden="true" />}
            description={t('totalTokensDesc')}
          />
          <KPICard
            title={t('activeKeys')}
            value={kpi ? kpi.active_keys.toLocaleString() : '—'}
            icon={<Key size={18} aria-hidden="true" />}
            description={kpi ? t('activeKeysDesc') : t('fetchFailed')}
          />
          <KPICard
            title={t('activeModels')}
            value={kpi ? kpi.active_models.toLocaleString() : '—'}
            icon={<Cpu size={18} aria-hidden="true" />}
            description={kpi ? t('activeModelsDesc') : t('fetchFailed')}
          />
        </div>
      </section>
    </div>
  );
}

async function TrendAndDistribution({ period, client }: { period: string; client: string }) {
  const t = await getTranslations('dashboard');
  const [analyticsResult, shareResult, teamsResult] = await Promise.allSettled([
    fetchAnalytics(period, 'team', client),
    fetchModelShare(period, 'all', client),
    fetchTeamOptions(),
  ]);

  const analytics =
    analyticsResult.status === 'fulfilled' ? analyticsResult.value : { trends: [], trends_by_team: [] };
  const initialShare =
    shareResult.status === 'fulfilled'
      ? shareResult.value
      : { period, team_id: 'all', total_cost_usd: 0, models: [] };
  const teams = teamsResult.status === 'fulfilled' ? teamsResult.value : [];

  return (
    <section className="space-y-3">
      <h2 className="px-1 text-xs font-bold uppercase tracking-[0.12em] text-muted-foreground">
        {t('trendAndDistribution')}
      </h2>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.6fr_1fr]">
        <CostTrendCard trends={analytics.trends ?? []} trendsByTeam={analytics.trends_by_team ?? []} />
        <ModelShareDonutClient initialData={initialShare} teams={teams} period={period} client={client} />
      </div>
    </section>
  );
}

async function TeamUserRanking({ period, client }: { period: string; client: string }) {
  const t = await getTranslations('dashboard');
  // §60.9: 팀·사용자 모두 실제 비용(usage_logs SUCCESS+KST) 기준으로 통일 — 예산
  // 설정 여부와 무관히 진짜 top spender 를 보여준다(기존 budgets/summary 소스는
  // 예산설정 대상만 포함해 누락 위험). 예산 소진율(%)은 budgets/summary 에서 보강.
  const [topTeamsResult, topUsersResult, budgetResult] = await Promise.allSettled([
    fetchTopTeams(period, 5, client),
    fetchTopUsers(period, 5, client),
    fetchBudgetSummary(period),
  ]);
  const topTeams = topTeamsResult.status === 'fulfilled' ? topTeamsResult.value : [];
  const topUsers = topUsersResult.status === 'fulfilled' ? topUsersResult.value : [];
  const budgetItems = budgetResult.status === 'fulfilled' ? budgetResult.value : [];

  // target_id(팀/사용자 uuid) 기준 예산 소진율/한도 룩업 — 이름 매칭은 부서 접두어
  // 유무 등으로 어긋날 수 있어 id 기준이 안전(§60.9 팔로업).
  const teamPctById = new Map<string, number | null>(
    budgetItems
      .filter((i) => i.target_type === 'team')
      .map((i) => [i.target_id, i.usage_pct != null ? parseFloat(i.usage_pct) : null]),
  );
  // target_id(팀 uuid) → 예산 한도/사용액. "팀 예산 적용" 표기 시 "얼마나 쓰고
  // 있는지"까지 병기하기 위함(팀명만으론 한눈에 안 보인다는 피드백 반영).
  // (팀명 매칭은 부서 접두어 유무 등으로 어긋날 수 있어 id 기준이 안전).
  const teamLimitById = new Map<string, number | null>(
    budgetItems
      .filter((i) => i.target_type === 'team')
      .map((i) => [i.target_id, i.limit_usd != null ? parseFloat(i.limit_usd) : null]),
  );
  const teamUsedById = new Map<string, number>(
    budgetItems
      .filter((i) => i.target_type === 'team')
      .map((i) => [i.target_id, parseFloat(i.used_usd || '0')]),
  );
  const userPctById = new Map<string, number | null>(
    budgetItems
      .filter((i) => i.target_type === 'user')
      .map((i) => [i.target_id, i.usage_pct != null ? parseFloat(i.usage_pct) : null]),
  );

  const teamRows: TopSpendRow[] = topTeams.map((tm) => ({
    id: tm.team_id,
    name: tm.name,
    usedUsd: tm.cost_usd,
    usagePct: teamPctById.get(tm.team_id) ?? null,
  }));

  // 실제 비용 기준. 본인 예산이 없더라도(usagePct=null) 소속 팀에 예산이 설정돼
  // 있으면 "팀 예산 적용"으로 표기(§60.9 팔로업) — 완전 무예산과 구분. 소속 팀명을
  // 이름 아래 병기하고, 팀 예산 적용 시엔 팀의 사용액/한도도 함께 보여줘 어떤
  // 유저가 어떤 팀에서 얼마를 쓰는지 한눈에 보이게 한다.
  const userRows: TopSpendRow[] = topUsers.map((u) => {
    const usagePct = userPctById.get(u.user_id) ?? null;
    const teamLimit = u.team_id ? teamLimitById.get(u.team_id) ?? null : null;
    const teamBudgetApplied = usagePct == null && !!u.team_id && teamLimit != null;
    return {
      id: u.user_id,
      name: u.name || u.email,
      subtitle: u.team_name,
      usedUsd: u.cost_usd,
      usagePct,
      teamBudgetApplied,
      teamBudget:
        teamBudgetApplied && u.team_id && teamLimit != null
          ? { used: teamUsedById.get(u.team_id) ?? 0, limit: teamLimit }
          : null,
    };
  });

  return (
    <section className="space-y-3">
      <h2 className="px-1 text-xs font-bold uppercase tracking-[0.12em] text-muted-foreground">
        {t('teamUserRanking')}
      </h2>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <TopSpendTable
          title={t('topTeamByCost')}
          subtitle={t('topTeamByCostSubtitle')}
          rows={teamRows}
          accentVar="var(--chart-1)"
          teamBudgetAppliedLabel={t('teamBudgetApplied')}
        />
        <TopSpendTable
          title={t('topUserByCost')}
          subtitle={t('topUserByCostSubtitle')}
          rows={userRows}
          accentVar="var(--chart-2)"
          teamBudgetAppliedLabel={t('teamBudgetApplied')}
        />
      </div>
    </section>
  );
}

async function ClientDistribution({ period }: { period: string }) {
  const t = await getTranslations('dashboard');
  const share = await fetchClientShare(period).catch(() => null);
  const data: ClientShareResponse = share ?? { period, total_cost_usd: 0, clients: [] };
  return (
    <section className="space-y-3">
      <h2 className="px-1 text-xs font-bold uppercase tracking-[0.12em] text-muted-foreground">
        {t('clientCostShare')}
      </h2>
      <div className="glass glass-hover rounded-apple p-5">
        <ClientShareDonutClient data={data} />
      </div>
    </section>
  );
}

const ALLOWED_CLIENTS = ['all', 'claude-code', 'cowork', 'codex', 'other'];

export default async function DashboardPage({
  searchParams,
}: {
  searchParams: { period?: string; client?: string };
}) {
  // 기간 해석은 한 번만. ?period 가 데이터 있는 월이면 존중, 아니면 latest(데이터
  // 있는 가장 최근 월)로. Next 14.2.29 — searchParams 는 sync 객체(await 금지).
  const { periods, latest } = await fetchAvailablePeriods();
  const requested = searchParams?.period;
  const period = requested && periods.includes(requested) ? requested : latest;

  const rawClient = searchParams?.client;
  const client = rawClient && ALLOWED_CLIENTS.includes(rawClient) ? rawClient : 'all';

  const t = await getTranslations('dashboard');

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold tracking-tight">{t('title')}</h1>
        <div className="flex flex-wrap items-center gap-2">
          <ClientFilter current={client} />
          <PeriodSelector periods={periods} current={period} />
        </div>
      </div>

      <Suspense
        key={`kpi-${period}-${client}`}
        fallback={
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
              <SkeletonCard count={4} />
            </div>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
              <SkeletonCard count={4} />
            </div>
          </div>
        }
      >
        <DashboardKPIs period={period} client={client} />
      </Suspense>

      <Suspense
        key={`trend-${period}-${client}`}
        fallback={<div className="glass rounded-apple h-72 animate-pulse" />}
      >
        <TrendAndDistribution period={period} client={client} />
      </Suspense>

      <Suspense
        key={`rank-${period}-${client}`}
        fallback={<div className="glass rounded-apple h-64 animate-pulse" />}
      >
        <TeamUserRanking period={period} client={client} />
      </Suspense>

      <Suspense
        key={`client-${period}`}
        fallback={<div className="glass rounded-apple h-64 animate-pulse" />}
      >
        <ClientDistribution period={period} />
      </Suspense>
    </div>
  );
}
