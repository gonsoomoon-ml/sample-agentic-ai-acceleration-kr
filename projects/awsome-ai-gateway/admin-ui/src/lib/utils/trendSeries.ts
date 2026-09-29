// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

/**
 * 비용 추이 멀티 시리즈 조립 — 대시보드(recharts)와 분석(Chart.js)이 공유한다.
 *
 * 입력: /admin/analytics 의 trends[](합계) + trends_by_team[](팀별 일별).
 * 출력은 "날짜 축 × 시리즈"의 열(column) 형태라 차트 라이브러리에 무관하다 —
 * recharts 는 행으로, Chart.js 는 dataset 으로 각자 변환해 쓴다.
 *
 * 규칙:
 *  - 날짜 축 = 합계 + 모든 팀 포인트의 날짜 합집합(오름차순). 어느 시리즈든
 *    그 날짜에 포인트가 없으면 null — 0 으로 접으면 "그날 비용 0" 이라는 거짓
 *    사실이 되므로 차트에서 끊어 그린다(recharts connectNulls / Chart.js spanGaps).
 *  - selectedIds 에 든 팀만 개별 시리즈. 나머지 팀은 날짜별 합산 'other'
 *    시리즈 하나로 접는다 — 꺼둔 팀의 비용이 화면에서 사라지지 않는다.
 *  - 합계 시리즈는 trends 값 그대로 — 팀 시리즈 합산으로 재계산하지 않는다.
 */

export interface TrendPoint {
  date: string;
  cost_usd: number;
  requests?: number;
}

export interface TeamTrendSeries {
  team: string;
  team_id: string;
  /** 팀의 소속 부서명 — 있으면 라벨을 "부서-팀"으로 표시한다. */
  dept_name?: string | null;
  points: TrendPoint[];
}

// 기본으로 개별 표시할 팀 수 — 이만큼이면 범례 한 줄 + 선 구분이 유지된다.
export const TOP_TEAM_COUNT = 5;

// 팀 시리즈 색상 — chart 토큰 우선, 이후는 팔레트 확장 보조색.
export const TEAM_COLORS = [
  'hsl(var(--chart-2))',   // sky
  'hsl(var(--chart-3))',   // pink
  'hsl(var(--chart-4))',   // violet
  'hsl(var(--chart-5))',   // amber
  'hsl(350 85% 66%)',      // rose
  'hsl(150 65% 42%)',      // green
  'hsl(230 75% 66%)',      // indigo
  'hsl(185 75% 42%)',      // cyan
  'hsl(25 90% 55%)',       // orange
  'hsl(280 60% 60%)',      // purple
];

// 팀별 선형 — 색뿐 아니라 대시 패턴도 달리해 단색 출력/색약에도 구분되게.
export const TEAM_DASHES = ['0', '8 4', '4 4', '10 4 2 4', '3 3', '12 4', '6 2 2 2', '2 5', '9 3 3 3', '5 5'];

export interface TeamSeriesMeta {
  key: string;
  teamId: string;
  name: string;
  color: string;
  dash: string;
  total: number;
}

export interface TrendSeriesSet {
  /** x축 날짜(오름차순) — 모든 values 배열이 이 순서에 정렬된다. */
  dates: string[];
  /** 합계 시리즈 값(날짜 없으면 null). */
  total: (number | null)[];
  /** 켜진 팀 시리즈 — dates 와 같은 길이의 values. */
  series: (TeamSeriesMeta & { values: (number | null)[] })[];
  /** 칩 렌더용 전체 팀 목록(비용 내림차순, 색/선형 부여 완료). */
  allTeams: TeamSeriesMeta[];
  /** 꺼진 팀들의 날짜별 합산 — 꺼진 팀이 없으면 null. */
  other: (number | null)[] | null;
  hasTeamSeries: boolean;
}

interface RankedTeam {
  teamId: string;
  name: string;
  total: number;
  byDate: Map<string, number>;
}

/** 표시용 팀 라벨 — 부서가 있으면 "부서-팀", 없으면 팀명 그대로. */
export function teamDisplayName(tt: Pick<TeamTrendSeries, 'team' | 'dept_name'>): string {
  return tt.dept_name ? `${tt.dept_name}-${tt.team}` : tt.team;
}

function rankTeams(trendsByTeam: TeamTrendSeries[]): RankedTeam[] {
  return trendsByTeam
    .map((tt) => ({
      teamId: tt.team_id,
      name: teamDisplayName(tt),
      total: tt.points.reduce((s, p) => s + Number(p.cost_usd || 0), 0),
      byDate: new Map(tt.points.map((p) => [p.date, Number(p.cost_usd)])),
    }))
    .sort((a, b) => b.total - a.total);
}

/** 기본 선택 = 비용 상위 TOP_TEAM_COUNT 개 팀 id. */
export function defaultSelectedTeamIds(trendsByTeam: TeamTrendSeries[]): Set<string> {
  return new Set(rankTeams(trendsByTeam).slice(0, TOP_TEAM_COUNT).map((t) => t.teamId));
}

export function buildTrendSeries(
  trends: TrendPoint[],
  trendsByTeam: TeamTrendSeries[],
  selectedIds?: ReadonlySet<string>,
  /**
   * 팀 색 팔레트 — 기본은 CSS 변수(hsl(var(--chart-N)))라 SVG(recharts)용.
   * Chart.js canvas 는 CSS 변수를 못 읽으므로 실색 배열을 넘겨야 한다.
   */
  colors: readonly string[] = TEAM_COLORS,
): TrendSeriesSet {
  const ranked = rankTeams(trendsByTeam);
  const selected = selectedIds ?? new Set(ranked.slice(0, TOP_TEAM_COUNT).map((t) => t.teamId));

  const totalByDate = new Map(trends.map((p) => [p.date, Number(p.cost_usd)]));
  const dateSet = new Set<string>(totalByDate.keys());
  for (const s of ranked) for (const d of s.byDate.keys()) dateSet.add(d);
  const dates = [...dateSet].sort();

  const on = ranked.filter((t) => selected.has(t.teamId));
  const off = ranked.filter((t) => !selected.has(t.teamId));

  const allTeams = ranked.map((t, i) => ({
    key: `team_${t.teamId}`,
    teamId: t.teamId,
    name: t.name,
    color: colors[i % colors.length],
    dash: TEAM_DASHES[i % TEAM_DASHES.length],
    total: t.total,
  }));
  const metaById = new Map(allTeams.map((m) => [m.teamId, m]));

  return {
    dates,
    total: dates.map((d) => totalByDate.get(d) ?? null),
    series: on.map((t) => ({
      ...metaById.get(t.teamId)!,
      values: dates.map((d) => t.byDate.get(d) ?? null),
    })),
    allTeams,
    other:
      off.length > 0
        ? dates.map((d) => off.reduce((s, tt) => s + (tt.byDate.get(d) ?? 0), 0))
        : null,
    hasTeamSeries: ranked.length > 0,
  };
}
