'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.


import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslations } from 'next-intl';
import {
  Chart as ChartJS,
  ArcElement,
  Tooltip,
  Legend,
} from 'chart.js';
import type { ActiveElement, ChartEvent } from 'chart.js';
import { Doughnut } from 'react-chartjs-2';
import type { ModelShareResponse, TeamOption } from '@/lib/actions/dashboard';
import { CATEGORICAL_PALETTE } from '@/lib/utils/chartTheme';
import { modelDisplay } from '@/lib/utils/modelLabel';
import { redirectToLoginIfUnauthorized } from '@/lib/utils/unauthorized';

ChartJS.register(ArcElement, Tooltip, Legend);

// 항목 구분용 다색 카테고리 팔레트 (색맹 안전, 라이트/다크 공통).
const COLORS = CATEGORICAL_PALETTE;

interface Props {
  initialData: ModelShareResponse;
  teams: TeamOption[];
  period: string;
  client?: string;  // 대시보드 앱 필터(?client=) — team 변경 재조회 시에도 유지.
}

export function ModelShareDonutClient({ initialData, teams, period, client }: Props) {
  const t = useTranslations('dashboard');
  const [teamId, setTeamId] = useState<string>('all');
  const [data, setData] = useState<ModelShareResponse>(initialData);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const isFirstRender = useRef(true);

  useEffect(() => {
    // 첫 렌더는 SSR 로 받은 initialData 를 그대로 쓴다 — fetch 생략.
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    const params = new URLSearchParams({ period, team_id: teamId });
    if (client && client !== 'all') params.set('client', client);
    fetch(`/api/dashboard/model-share?${params}`)
      .then(async (r) => {
        // ⚠️ 401 은 화면에 찍을 문자열이 아니다. 이 fetch 는 문서 내비게이션이 아니라
        //    필터 변경으로 도는 것이어서 middleware 의 만료 분기가 아예 실행되지 않는다
        //    (middleware 는 `/api/` 를 공개 경로로 통과시킨다). 예전엔 여기서
        //    "조회 실패: HTTP 401" 을 렌더하고 **낡은 숫자를 그대로 남겼다** —
        //    사용자는 로그인으로 돌아갈 길조차 없었다. 세션이 죽었으면 로그인으로 보낸다.
        //    403(권한 부족)은 이 분기에 걸리지 않고 아래 에러 문구로 간다 — 정상이다.
        if (redirectToLoginIfUnauthorized(r)) return null;
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return (await r.json()) as ModelShareResponse;
      })
      .then((next) => {
        // next === null 은 401 리다이렉트 경로 — 낡은 데이터를 덮어쓰지 않고 빠진다.
        if (!cancelled && next) setData(next);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : t('fetchFailedShort'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [teamId, period, client]);

  const chartData = useMemo(() => {
    return {
      labels: data.models.map((m) => modelDisplay(m.model_alias, m.display_name)),
      datasets: [
        {
          data: data.models.map((m) => m.cost_usd),
          backgroundColor: data.models.map((_, i) => COLORS[i % COLORS.length]),
          // 세그먼트 주변에 선/테두리/gap 일절 없음 (사용자 지시). 조각 직접 맞닿음.
          // 시인성은 (1) 색 자체 + (2) hover 강조 + (3) 가운데 1위 모델 표기로 확보.
          borderWidth: 0,
          spacing: 0,
          // hover 시 해당 조각만 바깥으로 튀어나와 강조 (정적 선 아님).
          hoverOffset: 14,
        },
      ],
    };
  }, [data]);

  const options = useMemo(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      cutout: '62%',
      // hover 시 부드럽게 튀어나오는 애니메이션.
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

  const isEmpty = data.models.length === 0 || data.total_cost_usd === 0;

  return (
    <div className="glass glass-hover rounded-apple p-5">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <div className="text-sm font-semibold tracking-tight">{t('modelShareTitle')}</div>
          <div className="text-xs text-muted-foreground">{t('modelShareSubtitle')}</div>
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="team-select" className="text-xs text-muted-foreground">
            {t('scope')}
          </label>
          <select
            id="team-select"
            value={teamId}
            onChange={(e) => setTeamId(e.target.value)}
            className="rounded-apple-sm border border-input bg-background px-2 py-1 text-xs interactive focus:outline-none focus:ring-1 focus:ring-ring"
            disabled={loading}
          >
            <option value="all">{t('scopeAll')}</option>
            {teams.map((team) => (
              <option key={team.id} value={team.id}>
                {team.department_name ? `${team.name} (${team.department_name})` : team.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      {error && (
        <p className="text-sm text-destructive mb-2">{t('fetchFailedWith', { error })}</p>
      )}

      {isEmpty ? (
        <div className="flex items-center justify-center h-64 text-sm text-muted-foreground">
          {t('noUsageForScope')}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-center">
          <div className="relative h-64">
            <Doughnut data={chartData} options={options} />
            {/* 가운데: 기본은 점유율 1위(models 는 비용 desc 정렬, [0]=1위),
                조각 호버 시에는 그 조각의 수치로 바뀐다 — 툴팁을 대체. */}
            {(() => {
              const centerItem = (hoverIndex != null ? data.models[hoverIndex] : null) ?? data.models[0];
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
                    {centerItem ? modelDisplay(centerItem.model_alias, centerItem.display_name) : ''}
                  </p>
                  <p className="mt-0.5 text-[10px] text-muted-foreground tabular-nums">
                    {hoverIndex != null && centerItem
                      ? `$${centerItem.cost_usd.toFixed(4)}`
                      : t('topShare', { total: data.total_cost_usd.toLocaleString('en-US', {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2,
                        }) })}
                  </p>
                </div>
              );
            })()}
          </div>
          <ul className="space-y-2">
            {data.models.map((m, i) => (
              <li
                key={m.model_alias}
                className={`grid grid-cols-[minmax(0,1fr)_5rem_3.5rem] items-center gap-2 text-sm rounded-apple-sm px-2 py-0.5 -mx-2 transition-colors ${i === hoverIndex ? 'bg-accent' : ''}`}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span
                    className="w-3 h-3 rounded-full flex-shrink-0"
                    style={{ backgroundColor: COLORS[i % COLORS.length] }}
                  />
                  {/* truncate 금지 — 모델명이 "Claude …" 로 잘려 구분이 안 된다. 줄바꿈 허용. */}
                  <span className="font-medium break-words leading-snug" title={modelDisplay(m.model_alias, m.display_name)}>
                    {modelDisplay(m.model_alias, m.display_name)}
                  </span>
                </div>
                <span className="tabular-nums text-xs text-right">
                  ${m.cost_usd.toFixed(2)}
                </span>
                <span className="text-muted-foreground tabular-nums text-xs text-right">
                  {m.share_pct.toFixed(1)}%
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}