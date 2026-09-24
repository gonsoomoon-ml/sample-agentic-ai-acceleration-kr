'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

// 사용량 트렌드 차트 — rate-limits 패널에서 노드 선택 시 표시.
// usage_logs 의 버킷 집계(/api/rate-limits/usage-trend)를 분당·시간당 비율로
// 정규화해, 설정 가능한 한도(RPM/TPM/CPH)와 같은 단위로 나란히 보여준다.
// 한도가 설정돼 있으면 기준선(ReferenceLine)을 함께 그린다.
// 데이터는 노드/윈도우 변경 시에만 fetch — 폴링은 live RPM 카드가 담당.

import { useEffect, useMemo, useState } from 'react';
import { useTranslations } from 'next-intl';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
} from 'recharts';
import {
  fetchRateLimitTrend,
  type TrendWindow,
  type UsageTrend,
} from '@/lib/utils/rateLimitUsage';

const WINDOWS: TrendWindow[] = ['1h', '6h', '24h', '7d'];
const WINDOW_LABEL: Record<TrendWindow, string> = {
  '1h': '1h',
  '6h': '6h',
  '24h': '24h',
  '7d': '7d',
};

type Metric = 'rpm' | 'tpm' | 'cph';

interface UsageTrendChartProps {
  scope: string;
  scopeId: string;
  limits: {
    rpm?: number | null;
    tpm?: number | null;
    cph?: number | null;
  };
}

function fmtTick(t: number, bucketSec: number): string {
  const d = new Date(t * 1000);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  if (bucketSec >= 3600) {
    return `${d.getMonth() + 1}/${d.getDate()} ${hh}시`;
  }
  return `${hh}:${mm}`;
}

function fmtValue(v: number, metric: Metric): string {
  if (metric === 'cph') return `$${v >= 10 ? v.toFixed(0) : v.toFixed(3)}`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return v >= 10 ? v.toFixed(0) : v.toFixed(1);
}

export function UsageTrendChart({ scope, scopeId, limits }: UsageTrendChartProps) {
  const t = useTranslations('rateLimits');
  const [window, setWindow] = useState<TrendWindow>('24h');
  const [metric, setMetric] = useState<Metric>('rpm');
  const [trend, setTrend] = useState<UsageTrend | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetchRateLimitTrend(scope, scopeId, window)
      .then((u) => {
        if (alive) setTrend(u);
      })
      .catch(() => {
        if (alive) setTrend(null);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [scope, scopeId, window]);

  // 버킷 원시값 → 한도와 같은 단위로 정규화 (rpm/tpm=분당, cph=시간당).
  const data = useMemo(() => {
    if (!trend?.available) return [];
    const b = trend.bucket_sec;
    return trend.points.map((p) => ({
      t: p.t,
      rpm: (p.requests * 60) / b,
      tpm: (p.tokens * 60) / b,
      cph: (p.cost_usd * 3600) / b,
    }));
  }, [trend]);

  const limit = limits[metric] ?? null;
  const hasData = data.some((p) => p[metric] > 0);

  return (
    <div className="mb-4 rounded-md border border-border bg-card/50 px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-muted-foreground">{t('usageTrend')}</span>
        <div className="flex items-center gap-1">
          {WINDOWS.map((w) => (
            <button
              key={w}
              type="button"
              onClick={() => setWindow(w)}
              className={`rounded-md px-2 py-0.5 text-xs font-medium transition-colors ${
                window === w
                  ? 'bg-primary/15 text-primary'
                  : 'text-muted-foreground hover:bg-accent'
              }`}
            >
              {WINDOW_LABEL[w]}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-1 flex items-center gap-1">
        {(['rpm', 'tpm', 'cph'] as Metric[]).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setMetric(m)}
            className={`rounded-md px-2 py-0.5 text-xs font-medium transition-colors ${
              metric === m
                ? 'bg-primary/15 text-primary'
                : 'text-muted-foreground hover:bg-accent'
            }`}
          >
            {m.toUpperCase()}
          </button>
        ))}
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">{t('usageTrendDesc')}</p>

      <div className="mt-2 h-[150px]">
        {loading && !trend ? (
          <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
            …
          </div>
        ) : !trend?.available ? null : !hasData ? (
          <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
            {t('trendEmpty')}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 4, right: 4, left: -18, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border" vertical={false} />
              <XAxis
                dataKey="t"
                tickFormatter={(v) => fmtTick(v, trend.bucket_sec)}
                tick={{ fontSize: 10 }}
                tickLine={false}
                axisLine={false}
                minTickGap={40}
              />
              <YAxis
                tick={{ fontSize: 10 }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v) => fmtValue(Number(v), metric)}
                width={44}
              />
              <Tooltip
                labelFormatter={(v) => fmtTick(Number(v), trend.bucket_sec)}
                formatter={(v) => [fmtValue(Number(v), metric), metric.toUpperCase()]}
                contentStyle={{
                  backgroundColor: 'hsl(var(--card))',
                  border: '1px solid hsl(var(--border))',
                  borderRadius: 'var(--radius)',
                  fontSize: 11,
                }}
                labelStyle={{ color: 'hsl(var(--muted-foreground))' }}
                itemStyle={{ color: 'hsl(var(--card-foreground))' }}
              />
              {limit != null && limit > 0 && (
                <ReferenceLine
                  y={limit}
                  // 기본 ifOverflow='discard' 는 한도가 Y축 범위를 넘으면 선을
                  // 아예 안 그린다 — 한도 비교가 이 차트의 목적이므로 축을 확장한다.
                  ifOverflow="extendDomain"
                  stroke="hsl(var(--destructive, 0 84% 60%))"
                  strokeDasharray="4 3"
                  label={{
                    value: `${t('trendLimit')} ${fmtValue(limit, metric)}`,
                    position: 'insideTopRight',
                    fontSize: 10,
                  }}
                />
              )}
              <Area
                type="monotone"
                dataKey={metric}
                stroke="hsl(var(--primary))"
                fill="hsl(var(--primary))"
                fillOpacity={0.12}
                strokeWidth={1.5}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
