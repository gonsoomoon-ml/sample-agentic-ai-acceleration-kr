// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

// 실시간 RPM 사용량(§60.9) 클라이언트 폴링 — RateLimitConfigPanel 에서 사용.
// gateway-proxy 가 Redis 에 적재하는 sliding-window 카운터를 /api/rate-limits/usage
// 프록시 경유로 읽는다. fail-soft: 실패 시 available:false.

export interface RateLimitUsage {
  available: boolean;
  scope?: string;
  scope_id?: string;
  window_sec: number;
  rpm_used_total: number;
  by_model: { model_alias: string; rpm_used: number }[];
  reason?: string;
}

export async function fetchRateLimitUsage(scope: string, scopeId: string): Promise<RateLimitUsage> {
  const params = new URLSearchParams({ scope, scope_id: scopeId });
  try {
    const res = await fetch(`/api/rate-limits/usage?${params}`, { cache: 'no-store' });
    const data = await res.json();
    return {
      available: !!data.available,
      scope: data.scope,
      scope_id: data.scope_id,
      window_sec: data.window_sec ?? 60,
      rpm_used_total: data.rpm_used_total ?? 0,
      by_model: Array.isArray(data.by_model) ? data.by_model : [],
      reason: data.reason,
    };
  } catch {
    return { available: false, window_sec: 60, rpm_used_total: 0, by_model: [] };
  }
}

// ── 사용량 트렌드(usage_logs 버킷 집계) ──
// RateLimitConfigPanel 의 트렌드 차트용. 버킷당 원시값(requests/tokens/cost_usd)을
// 돌려주고, 분당·시간당 정규화는 표시 쪽에서 bucket_sec 으로 계산한다.

export interface UsageTrendPoint {
  t: number; // epoch seconds (버킷 시작, UTC)
  requests: number;
  tokens: number;
  cost_usd: number;
}

export interface UsageTrend {
  available: boolean;
  window_sec: number;
  bucket_sec: number;
  points: UsageTrendPoint[];
  reason?: string;
}

export type TrendWindow = '1h' | '6h' | '24h' | '7d';

export async function fetchRateLimitTrend(
  scope: string,
  scopeId: string,
  window: TrendWindow,
): Promise<UsageTrend> {
  const params = new URLSearchParams({ scope, scope_id: scopeId, window });
  try {
    const res = await fetch(`/api/rate-limits/usage-trend?${params}`, { cache: 'no-store' });
    const data = await res.json();
    return {
      available: !!data.available,
      window_sec: data.window_sec ?? 0,
      bucket_sec: data.bucket_sec ?? 60,
      points: Array.isArray(data.points) ? data.points : [],
      reason: data.reason,
    };
  } catch {
    return { available: false, window_sec: 0, bucket_sec: 60, points: [] };
  }
}
