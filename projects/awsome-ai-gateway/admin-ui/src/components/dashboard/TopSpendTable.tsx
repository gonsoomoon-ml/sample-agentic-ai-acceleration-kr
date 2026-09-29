'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

/**
 * Top spenders ranking table (teams or users).
 * 실데이터: /admin/budgets/summary 의 BudgetSummaryItem[] 에서
 * target_type 으로 필터 → used_usd 내림차순 정렬 → 상위 N.
 * 데이터가 없으면 빈 상태 문구를 표시(가짜 행 없음).
 */

import { useTranslations } from 'next-intl';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/common/Table';

interface TopSpendRow {
  id: string;
  name: string;
  /** 이름 아래 작게 표시하는 보조 정보 (사용자 행의 소속 팀명 등). */
  subtitle?: string | null;
  usedUsd: number;
  usagePct: number | null;
  /** usagePct 가 null 인 이유가 "본인 예산 미설정 + 팀 예산 적용"인 경우 true.
   *  순수 무예산(팀도 예산 없음)과 구분해 "팀 예산 적용" 문구를 보여준다. */
  teamBudgetApplied?: boolean;
  /** teamBudgetApplied 일 때 팀 예산 사용액/한도 — "팀 예산 적용" 문구만으론
   *  실제 얼마나 쓰고 있는지 안 보여서 한눈에 보이도록 병기. */
  teamBudget?: { used: number; limit: number } | null;
}

interface TopSpendTableProps {
  title: string;
  subtitle?: string;
  rows: TopSpendRow[];
  /** 진행바 색상 (chart 토큰). */
  accentVar?: string;
  noBudgetLabel?: string;
  teamBudgetAppliedLabel?: string;
}

// $1.55 처럼 소수점까지 보여야 소액 사용자 비용이 뭉개지지 않는다(이전엔 정수 반올림으로
// $1 미만 차이가 전부 "$1"로 보여 실사용액을 구분 못 했다).
function fmtUsd(n: number): string {
  return `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function TopSpendTable({
  title,
  subtitle,
  rows,
  accentVar = 'var(--chart-1)',
  noBudgetLabel,
  teamBudgetAppliedLabel,
}: TopSpendTableProps) {
  const t = useTranslations('dashboard');
  return (
    <div className="glass glass-hover rounded-apple p-5">
      <div className="mb-1 text-sm font-semibold tracking-tight">{title}</div>
      {subtitle && <div className="mb-3 text-xs text-muted-foreground">{subtitle}</div>}

      {rows.length === 0 ? (
        <div className="py-8 text-center text-xs text-muted-foreground">
          {t('noDataForPeriod')}
        </div>
      ) : (
        <Table>
          <THead>
            <Tr>
              <Th>{t('name')}</Th>
              <Th numeric>{t('cost')}</Th>
              <Th>{t('budgetSpend')}</Th>
            </Tr>
          </THead>
          <TBody>
            {rows.map((r, i) => (
              <Tr key={r.id}>
                <Td emphasis>
                  <span className="flex items-center gap-2.5">
                    <span
                      className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold tabular-nums ${
                        i === 0
                          ? 'badge-teal'
                          : i === 1
                            ? 'badge-sky'
                            : i === 2
                              ? 'badge-amber'
                              : 'text-muted-foreground'
                      }`}
                    >
                      {i + 1}
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate">{r.name}</span>
                      {r.subtitle && (
                        <span className="block truncate text-[11px] font-normal text-muted-foreground">
                          {r.subtitle}
                        </span>
                      )}
                    </span>
                  </span>
                </Td>
                <Td numeric className="font-semibold">{fmtUsd(r.usedUsd)}</Td>
                <Td>
                  {r.usagePct != null ? (
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[--table-progress-track]">
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${Math.min(100, r.usagePct)}%`,
                            background: `hsl(${accentVar})`,
                          }}
                        />
                      </div>
                      <span className="w-9 text-right text-[11px] tabular-nums text-muted-foreground">
                        {r.usagePct.toFixed(0)}%
                      </span>
                    </div>
                  ) : r.teamBudgetApplied ? (
                    <span className="block max-w-[160px]">
                      <span className="block truncate text-[11px] text-muted-foreground italic">
                        {teamBudgetAppliedLabel ?? t('noBudgetSet')}
                      </span>
                      {r.teamBudget && (
                        <span className="block text-[11px] tabular-nums text-muted-foreground/80">
                          {fmtUsd(r.teamBudget.used)} / {fmtUsd(r.teamBudget.limit)}
                        </span>
                      )}
                    </span>
                  ) : (
                    <span className="text-[11px] text-muted-foreground">
                      {noBudgetLabel ?? t('noBudgetSet')}
                    </span>
                  )}
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      )}
    </div>
  );
}

export type { TopSpendRow };
