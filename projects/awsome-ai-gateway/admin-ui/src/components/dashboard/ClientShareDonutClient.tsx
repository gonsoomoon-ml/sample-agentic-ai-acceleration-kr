'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

import { useMemo, useState } from 'react';
import { useTranslations } from 'next-intl';
import {
  Chart as ChartJS,
  ArcElement,
  Tooltip,
  Legend,
} from 'chart.js';
import type { ActiveElement, ChartEvent } from 'chart.js';
import { Doughnut } from 'react-chartjs-2';
import type { ClientShareResponse } from '@/lib/actions/dashboard';
import { CATEGORICAL_PALETTE } from '@/lib/utils/chartTheme';
import { labelFor } from '@/lib/utils/modelLabel';

ChartJS.register(ArcElement, Tooltip, Legend);

// 항목 구분용 다색 카테고리 팔레트 (색맹 안전, 라이트/다크 공통).
const COLORS = CATEGORICAL_PALETTE;

interface Props {
  data: ClientShareResponse;
}

export function ClientShareDonutClient({ data }: Props) {
  const t = useTranslations('dashboard');
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const chartData = useMemo(
    () => ({
      labels: data.clients.map((c) => labelFor(c.client)),
      datasets: [
        {
          data: data.clients.map((c) => c.cost_usd),
          backgroundColor: data.clients.map((_, i) => COLORS[i % COLORS.length]),
          borderWidth: 0,
          spacing: 0,
          hoverOffset: 14,
        },
      ],
    }),
    [data],
  );

  const options = useMemo(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      cutout: '62%',
      animation: { animateRotate: true, animateScale: false },
      // 호버한 조각의 우측 목록 행도 함께 강조한다.
      onHover: (_event: ChartEvent, elements: ActiveElement[]) => {
        setHoverIndex(elements.length ? elements[0].index : null);
      },
      plugins: {
        legend: { display: false },
        // 캔버스 툴팁은 어디에 놓아도 도넛 중앙 오버레이를 물리적으로 가린다.
        // 호버 상세는 중앙 표시 자체가 담당(조각 호버 시 중앙 내용이 바뀐다).
        tooltip: { enabled: false },
      },
    }),
    [data],
  );

  if (!data.clients.length) {
    return (
      <div className="flex items-center justify-center h-64 text-sm text-muted-foreground">
        {t('noUsageForPeriod')}
      </div>
    );
  }

  return (
    // 카드가 전체 폭이라 grid-cols-2 는 도넛과 목록을 양끝으로 벌린다.
    // 도넛 고정폭 + 목록 컴팩트 컬럼을 중앙 정렬로 묶는다.
    <div className="flex flex-col md:flex-row items-center justify-center gap-6 md:gap-10">
      <div className="relative h-64 w-64 shrink-0">
        <Doughnut data={chartData} options={options} />
        {/* 가운데: 기본은 점유율 1위(clients 는 비용 desc 정렬, [0]=1위),
            조각 호버 시에는 그 조각의 수치로 바뀐다 — 툴팁을 대체. */}
        {(() => {
          const centerItem = (hoverIndex != null ? data.clients[hoverIndex] : null) ?? data.clients[0];
          const centerIdx = hoverIndex ?? 0;
          return (
            <div className="absolute inset-0 flex flex-col items-center justify-center px-6 text-center pointer-events-none">
              {centerItem && (
                <span
                  className="mb-1 h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: COLORS[centerIdx % COLORS.length] }}
                  aria-hidden="true"
                />
              )}
              <p className="text-2xl font-bold leading-none tracking-tight">
                {centerItem ? `${centerItem.share_pct.toFixed(0)}%` : '—'}
              </p>
              <p className="mt-1 max-w-full truncate text-xs font-medium text-foreground">
                {centerItem ? labelFor(centerItem.client) : ''}
              </p>
              <p className="mt-0.5 text-[10px] text-muted-foreground tabular-nums">
                {hoverIndex != null && centerItem
                  ? `$${centerItem.cost_usd.toFixed(2)}`
                  : t('topShare', { total: data.total_cost_usd.toLocaleString('en-US', {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    }) })}
              </p>
            </div>
          );
        })()}
      </div>
      <ul className="space-y-2 w-full md:w-[30rem]">
        {data.clients.map((c, i) => (
          <li
            key={c.client}
            // 이름 + 고정폭 우측정렬 수치 컬럼 — justify-between 은 이름과 수치가
            // 양끝으로 벌어지고 행마다 수치 위치가 들쭉날쭉해 산만해 보였다.
            className={`grid grid-cols-[minmax(0,1fr)_5rem_6.5rem_5rem_3.5rem] items-center gap-2 text-sm rounded-apple-sm px-2 py-0.5 -mx-2 transition-colors ${i === hoverIndex ? 'bg-accent' : ''}`}
          >
            <div className="flex items-center gap-2 min-w-0">
              <span
                className="w-3 h-3 rounded-full flex-shrink-0"
                style={{ backgroundColor: COLORS[i % COLORS.length] }}
              />
              <span className="font-medium break-words leading-snug" title={labelFor(c.client)}>{labelFor(c.client)}</span>
            </div>
            <span className="text-muted-foreground tabular-nums text-xs text-right">
              {t('callCount', { count: c.call_count })}
            </span>
            <span className="text-muted-foreground tabular-nums text-xs text-right">
              {c.web_search_count > 0 ? t('webSearchCount', { count: c.web_search_count }) : ''}
            </span>
            <span className="tabular-nums text-xs text-right">${c.cost_usd.toFixed(2)}</span>
            <span className="text-muted-foreground tabular-nums text-xs text-right">
              {c.share_pct.toFixed(1)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
