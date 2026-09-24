'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

import { useMemo, useState } from 'react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import { useTranslations } from 'next-intl';
import type { TrendDataPoint } from '@/types/entities';
import { CATEGORICAL_PALETTE, PRIMARY_SERIES, useChartTheme } from '@/lib/utils/chartTheme';
import {
  buildTrendSeries,
  defaultSelectedTeamIds,
  type TeamTrendSeries,
} from '@/lib/utils/trendSeries';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend
);

interface CostTrendChartClientProps {
  trends: TrendDataPoint[];
  trendsByTeam?: TeamTrendSeries[];
}

// 대시보드 CostTrendCard 와 같은 멀티 시리즈 — 합계(굵은 선) + 팀별(색·대시 구분)
// + 꺼진 팀은 '기타' 합산. 조립 규칙은 lib/utils/trendSeries 가 단일 소스.
// 팀 색은 CATEGORICAL_PALETTE[0](틸=합계색)을 제외한 실색 — Chart.js canvas 는
// CSS 변수(hsl(var(--x)))를 못 읽는다.
const TEAM_PALETTE = CATEGORICAL_PALETTE.slice(1);

// '8 4' → [8,4], '0'(실선) → [] — Chart.js borderDash 형식.
const dashToArray = (dash: string) => (dash === '0' ? [] : dash.split(' ').map(Number));

export function CostTrendChartClient({ trends, trendsByTeam = [] }: CostTrendChartClientProps) {
  const t = useTranslations('analytics');
  const theme = useChartTheme();
  const topIds = useMemo(() => defaultSelectedTeamIds(trendsByTeam), [trendsByTeam]);
  // null = 기본 선택(상위 N). 사용자가 칩을 누르면 명시 선택이 된다.
  const [picked, setPicked] = useState<Set<string> | null>(null);
  const effective = picked ?? topIds;
  const set = useMemo(
    () => buildTrendSeries(trends, trendsByTeam, effective, TEAM_PALETTE),
    [trends, trendsByTeam, effective],
  );

  const toggleTeam = (teamId: string) => {
    const next = new Set(effective);
    if (next.has(teamId)) next.delete(teamId);
    else next.add(teamId);
    setPicked(next);
  };

  const data = {
    labels: set.dates,
    datasets: [
      {
        label: t('costSeriesTotal'),
        data: set.total,
        borderColor: PRIMARY_SERIES,
        backgroundColor: 'rgba(45, 212, 191, 0.16)',
        fill: true,
        tension: 0.3,
        borderWidth: 3,
        pointRadius: 3,
        pointHoverRadius: 5,
        spanGaps: true,
      },
      ...set.series.map((s) => ({
        label: s.name,
        data: s.values,
        borderColor: s.color,
        borderDash: dashToArray(s.dash),
        fill: false,
        tension: 0.3,
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        spanGaps: true,
      })),
      ...(set.other
        ? [
            {
              label: t('costSeriesOther'),
              data: set.other,
              borderColor: theme.textMuted,
              borderDash: [2, 4],
              fill: false,
              tension: 0.3,
              borderWidth: 2,
              pointRadius: 0,
              pointHoverRadius: 4,
              spanGaps: true,
            },
          ]
        : []),
    ],
  };

  const options = {
    responsive: true,
    // 기본 'nearest' + intersect:true 는 포인트 정위치에만 툴팁이 뜨고,
    // pointRadius:0 인 팀 시리즈는 사실상 호버 불가. x축 기준 index 모드로
    // 바꿔 커서 아래 날짜의 전체 시리즈 값이 바로 뜨게 한다.
    interaction: { mode: 'index' as const, axis: 'x' as const, intersect: false },
    plugins: {
      legend: {
        position: 'bottom' as const,
        // 기본 onClick 은 데이터셋 토글 — 유일 시리즈를 끄면 빈 차트가 되는
        // 혼란을 없애기 위해 무력화(표시 제어는 위 칩이 담당).
        onClick: () => undefined,
        // 기본은 채색 박스 — 선 차트라 범례도 선(pointStyle 'line')으로.
        labels: {
          color: theme.text,
          usePointStyle: true,
          pointStyle: 'line',
        },
      },
      title: { display: true, text: t('usageTrend'), color: theme.text },
      tooltip: {
        // 도넛 차트와 같은 불투명 팝오버 스타일.
        backgroundColor: theme.surface,
        titleColor: theme.text,
        bodyColor: theme.textMuted,
        borderColor: theme.isDark ? 'rgba(255,255,255,0.16)' : 'rgba(15,23,42,0.12)',
        borderWidth: 1,
        padding: 10,
        cornerRadius: 8,
        // 합계가 위로 오도록 값 desc 정렬.
        itemSort: (a: import('chart.js').TooltipItem<'line'>, b: import('chart.js').TooltipItem<'line'>) =>
          (b.parsed.y ?? 0) - (a.parsed.y ?? 0),
        callbacks: {
          label: (ctx: import('chart.js').TooltipItem<'line'>) =>
            ` ${ctx.dataset.label}: $${(ctx.parsed.y ?? 0).toFixed(4)}`,
        },
      },
    },
    scales: {
      x: {
        title: { display: true, text: t('date'), color: theme.textMuted },
        ticks: { color: theme.textMuted },
        grid: { color: theme.grid },
      },
      y: {
        title: { display: true, text: 'USD', color: theme.textMuted },
        ticks: {
          color: theme.textMuted,
          callback: (value: string | number) => `$${Number(value).toFixed(2)}`,
        },
        grid: { color: theme.grid },
      },
    },
  };

  if (set.dates.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-sm text-muted-foreground">
        {t('noData')}
      </div>
    );
  }

  return (
    <div>
      {set.hasTeamSeries && (
        <div className="mb-3 flex flex-wrap gap-1.5">
          {set.allTeams.map((tm) => {
            const on = effective.has(tm.teamId);
            return (
              <button
                key={tm.teamId}
                type="button"
                aria-pressed={on}
                title={tm.name}
                onClick={() => toggleTeam(tm.teamId)}
                className={`inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] transition-colors ${
                  on
                    ? 'border-transparent bg-secondary text-foreground'
                    : 'border-border text-muted-foreground hover:bg-secondary/60'
                }`}
              >
                <span
                  className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: on ? tm.color : theme.textMuted }}
                />
                <span className="max-w-[12rem] truncate">{tm.name}</span>
              </button>
            );
          })}
        </div>
      )}
      <Line data={data} options={options} />
    </div>
  );
}
