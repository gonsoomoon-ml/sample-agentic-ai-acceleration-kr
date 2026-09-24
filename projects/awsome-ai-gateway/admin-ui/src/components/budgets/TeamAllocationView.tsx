'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

/**
 * 팀 리더 예산 배정 카드 — admin 의 BudgetSummaryTable 과 같은 열 구성
 * (이름/유형/최대예산/사용/잔여/사용률/상태).
 *
 *  - TEAM 행: admin 이 설정한 팀 총예산 — 읽기 전용(리더는 멤버 배정만 바꾼다).
 *  - USER 행: 최대예산 칸이 인라인 입력. 입력값은 문자열 draft 로 들고 있다가
 *    파싱해 숫자에 반영 — controlled number input 은 "0" 을 지우면 즉시 0 으로
 *    되돌아가 값을 새로 칠 수 없는 UX 문제가 있었다.
 *  - 꺼둔(비운) 칸은 0 으로 간주 — 합계가 팀 예산을 넘으면 저장 불가.
 */

import { useMemo, useState, useTransition } from 'react';
import { useTranslations } from 'next-intl';
import type { TeamBudgetAllocation, AllocationEntry } from '@/types/entities';
import { AlertLevel, BudgetScope } from '@/types/enums';
import { allocateTeamBudgetAction } from '@/lib/actions/budgets';
import { SpinnerButton } from '@/components/common/SpinnerButton';
import { useToast } from '@/components/common/ToastProvider';
import { Table, THead, TBody, TFoot, Tr, Th, Td, TEmpty } from '@/components/common/Table';
import { AlertBadge, RoleBadge, TypeBadge, UsageBar, alertLevelOf } from './budgetVisuals';

interface TeamAllocationViewProps {
  teamId: string;
  initialAllocation: TeamBudgetAllocation | null;
}

const parseDraft = (v: string | undefined): number => {
  const n = parseFloat(v ?? '');
  return Number.isFinite(n) && n >= 0 ? n : 0;
};

export function TeamAllocationView({ teamId, initialAllocation }: TeamAllocationViewProps) {
  const t = useTranslations('budgets');
  const tCommon = useTranslations('common');
  const { toast } = useToast();
  const [isPending, startTransition] = useTransition();

  const memberEntries = useMemo(
    () => (initialAllocation?.entries ?? []).filter((e) => e.target_type !== 'TEAM'),
    [initialAllocation],
  );
  const teamEntry = useMemo(
    () => (initialAllocation?.entries ?? []).find((e) => e.target_type === 'TEAM'),
    [initialAllocation],
  );

  // 문자열 draft — "0" 을 지우고 새 값을 칠 수 있게 한다(빈 칸은 0 취급).
  const [drafts, setDrafts] = useState<Record<string, string>>(() =>
    Object.fromEntries(memberEntries.map((e) => [e.target_id, String(e.allocated_usd)]))
  );

  const totalBudget = initialAllocation?.total_budget_usd ?? 0;
  const allocatedOf = (entry: AllocationEntry) => parseDraft(drafts[entry.target_id]);
  const totalAllocated = memberEntries.reduce((sum, entry) => sum + allocatedOf(entry), 0);
  const unallocated = totalBudget - totalAllocated;
  const isOverBudget = totalAllocated > totalBudget;

  const alertLabels: Record<string, string> = {
    [AlertLevel.NORMAL]: t('alertLevels.NORMAL'),
    [AlertLevel.WARNING]: t('alertLevels.WARNING'),
    [AlertLevel.CRITICAL]: t('alertLevels.CRITICAL'),
  };
  const typeLabels: Record<string, string> = {
    [BudgetScope.TEAM]: t('scope.TEAM'),
    [BudgetScope.USER]: t('scope.USER'),
  };
  const roleLabels: Record<string, string> = {
    ADMIN: tCommon('role.ADMIN'),
    TEAM_LEADER: tCommon('role.TEAM_LEADER'),
    DEVELOPER: tCommon('role.DEVELOPER'),
  };

  const handleSave = () => {
    const allocations = memberEntries.map((e) => ({
      ...e,
      allocated_usd: allocatedOf(e),
    }));
    startTransition(async () => {
      const result = await allocateTeamBudgetAction(teamId, allocations);

      if (result.success) {
        toast({
          type: 'success',
          message: t('allocationSaved'),
          auto_dismiss_ms: 3000,
        });
      } else {
        toast({
          type: 'error',
          message: result.error,
          auto_dismiss_ms: 5000,
        });
      }
    });
  };

  if (!initialAllocation) {
    return (
      <p className="text-sm text-muted-foreground">{t('allocationLoadFailed')}</p>
    );
  }

  const memberRow = (entry: AllocationEntry) => {
    const allocated = allocatedOf(entry);
    const remaining = allocated - entry.used_usd;
    const pct = allocated > 0 ? (entry.used_usd / allocated) * 100 : null;
    const level = alertLevelOf(pct);
    return (
      <Tr key={entry.target_id}>
        <Td emphasis>{entry.target_name}</Td>
        <Td>
          <RoleBadge role={entry.target_role} labels={roleLabels} />
        </Td>
        <Td numeric>
          <div className="inline-flex items-center gap-1">
            <span className="text-muted-foreground">$</span>
            <input
              type="number"
              min={0}
              step={0.01}
              value={drafts[entry.target_id] ?? ''}
              onChange={(e) =>
                setDrafts((prev) => ({ ...prev, [entry.target_id]: e.target.value }))
              }
              aria-label={t('colAllocated')}
              className="w-28 rounded-md border border-input bg-background px-2 py-1 text-sm tabular-nums text-right focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            />
          </div>
        </Td>
        <Td numeric>${entry.used_usd.toFixed(2)}</Td>
        <Td numeric className={remaining < 0 ? 'text-destructive' : undefined}>
          ${remaining.toFixed(2)}
        </Td>
        <Td>
          <div className="flex items-center gap-2">
            {pct != null ? (
              <>
                <UsageBar pct={pct} level={level} />
                <span className="w-12 text-right text-[11px] tabular-nums text-muted-foreground whitespace-nowrap">
                  {pct.toFixed(1)}%
                </span>
              </>
            ) : (
              <span className="text-xs text-muted-foreground italic">-</span>
            )}
          </div>
        </Td>
        <Td>
          <AlertBadge level={level} labels={alertLabels} />
        </Td>
      </Tr>
    );
  };

  const teamPct =
    teamEntry && totalBudget > 0 ? (teamEntry.used_usd / totalBudget) * 100 : null;
  const teamLevel = alertLevelOf(teamPct);

  return (
    <div className="space-y-4">
      <div className="w-full glass rounded-apple overflow-hidden">
        <Table>
          <THead>
            <Tr>
              <Th>{t('targetName')}</Th>
              <Th>{t('type')}</Th>
              <Th numeric>{t('maxBudget')}</Th>
              <Th numeric>{t('used')}</Th>
              <Th numeric>{t('remainingBudget')}</Th>
              <Th className="min-w-[120px]">{t('usageRate')}</Th>
              <Th>{t('status')}</Th>
            </Tr>
          </THead>
          <TBody>
            {/* admin 설정 팀 총예산 — 리더는 읽기만 */}
            <Tr className="bg-muted/20">
              <Td emphasis>
                <div className="flex items-center gap-2">
                  {initialAllocation.team_name}
                  <span className="text-[10px] text-muted-foreground">
                    {t('setByAdmin')}
                  </span>
                </div>
              </Td>
              <Td>
                <TypeBadge type={BudgetScope.TEAM} labels={typeLabels} />
              </Td>
              <Td numeric>${totalBudget.toFixed(2)}</Td>
              <Td numeric>${(teamEntry?.used_usd ?? 0).toFixed(2)}</Td>
              <Td numeric>${(teamEntry?.remaining_usd ?? totalBudget).toFixed(2)}</Td>
              <Td>
                <div className="flex items-center gap-2">
                  {teamPct != null ? (
                    <>
                      <UsageBar pct={teamPct} level={teamLevel} />
                      <span className="w-12 text-right text-[11px] tabular-nums text-muted-foreground whitespace-nowrap">
                        {teamPct.toFixed(1)}%
                      </span>
                    </>
                  ) : (
                    <span className="text-xs text-muted-foreground italic">-</span>
                  )}
                </div>
              </Td>
              <Td>
                <AlertBadge level={teamLevel} labels={alertLabels} />
              </Td>
            </Tr>
            {memberEntries.length === 0 ? (
              <TEmpty colSpan={7}>{t('noMembers')}</TEmpty>
            ) : (
              memberEntries.map(memberRow)
            )}
          </TBody>
          <TFoot>
            <Tr>
              <Td emphasis colSpan={4}>{t('unallocated')}</Td>
              <Td numeric className={isOverBudget ? 'text-destructive' : 'text-primary'}>
                ${unallocated.toFixed(2)}
              </Td>
              <Td colSpan={2} />
            </Tr>
          </TFoot>
        </Table>
      </div>

      {isOverBudget && (
        <p className="text-sm text-destructive" role="alert">
          {t('allocationExceeds', { allocated: totalAllocated.toFixed(2), budget: totalBudget.toFixed(2) })}
        </p>
      )}

      <div className="flex justify-end">
        <SpinnerButton
          onClick={handleSave}
          isLoading={isPending}
          disabled={isOverBudget || memberEntries.length === 0}
          type="button"
        >
          {tCommon('save')}
        </SpinnerButton>
      </div>
    </div>
  );
}
