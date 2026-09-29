// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

import type { AnalyticsFilterForm } from '@/types/api';
import { resolveMonth } from '@/lib/utils/period';

/**
 * `/admin/analytics` 질의 파라미터 빌더 — **단일 출처**.
 *
 * analytics 페이지의 여러 서버 컴포넌트(ROIMetricsCards/CostTrendChart/BreakdownChart)
 * 와 퀵챗 컨텍스트 등록(ContextSection)이 모두 같은 엔드포인트를 같은 파라미터로
 * 호출한다. Next.js **요청 메모이제이션**은 동일 렌더 내 동일 URL+옵션 fetch 를
 * 1회로 dedup 하므로, 모든 호출처가 이 함수를 거쳐 **바이트 동일 요청**을 만들어야
 * 중복 네트워크 호출이 발생하지 않는다(파라미터가 갈리면 dedup 이 조용히 깨짐).
 */
export function buildAnalyticsQuery(
  filter: AnalyticsFilterForm,
  latestMonth?: string,
): Record<string, string | number | undefined> {
  const query: Record<string, string | number | undefined> = {
    period: resolveMonth(filter, latestMonth),
    group_by: filter.group_by,
    scope: filter.scope ?? 'all',
  };
  // custom 직접 입력 — 두 날짜가 다 있을 때만 구간을 실어 보낸다. 백엔드는 둘이
  // 있으면 period 대신 일자 구간으로 집계한다. start 만 있는 진행 중 상태는
  // 그 날짜의 월 집계로 미리보기(resolveMonth 가 start 의 월을 뽑는다).
  if (filter.period === 'custom' && filter.start_date && filter.end_date) {
    query.start_date = filter.start_date;
    query.end_date = filter.end_date;
  }
  return query;
}
