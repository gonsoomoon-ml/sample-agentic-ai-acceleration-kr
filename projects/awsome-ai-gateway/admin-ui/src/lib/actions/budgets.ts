'use server';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.


import { revalidatePath } from 'next/cache';
import { z } from 'zod';
import { adminAPI } from '@/lib/api-client';
import { BudgetSetSchema } from '@/types/api';
import { withRetry } from '@/lib/utils/retry';
import { APIError } from '@/lib/utils/retry';
import type { AllocationEntry, TeamBudgetAllocation } from '@/types/entities';
import type { ActionResult } from './types';

// ─── setBudgetAction ──────────────────────────────────────────────────────────

export async function setBudgetAction(formData: unknown): Promise<ActionResult<void>> {
  const parsed = BudgetSetSchema.safeParse(formData);

  if (!parsed.success) {
    const fieldErrors: Record<string, string> = {};
    for (const issue of parsed.error.issues) {
      const key = issue.path.join('.');
      fieldErrors[key] = issue.message;
    }
    return { success: false, error: 'Validation failed', fieldErrors };
  }

  const { target_id, target_type, max_budget_usd, policy, alert_thresholds } = parsed.data;

  try {
    const endpoint =
      target_type === 'TEAM'
        ? `/admin/budgets/team/${target_id}`
        : `/admin/budgets/user/${target_id}`;

    await withRetry(() => adminAPI.put(endpoint, { max_budget_usd, policy, alert_thresholds }));
    revalidatePath('/budgets');
    return { success: true, data: undefined };
  } catch (err) {
    return { success: false, error: toErrorMessage(err) };
  }
}

// ─── deleteUserBudgetAction ───────────────────────────────────────────────────

export async function deleteUserBudgetAction(userId: string): Promise<ActionResult<void>> {
  try {
    await withRetry(() => adminAPI.delete(`/admin/budgets/user/${userId}`));
    revalidatePath('/budgets');
    return { success: true, data: undefined };
  } catch (err) {
    return { success: false, error: toErrorMessage(err) };
  }
}

// ─── allocateTeamBudgetAction ─────────────────────────────────────────────────

export async function allocateTeamBudgetAction(
  teamId: string,
  allocations: Pick<AllocationEntry, 'target_id' | 'target_type' | 'allocated_usd'>[]
): Promise<ActionResult<void>> {
  if (!teamId) {
    return { success: false, error: 'Team ID is required' };
  }

  try {
    // 백엔드 AllocateBudgetRequest 는 USER 행만 {user_id, allocated_usd} 로 기대한다.
    // AllocationEntry 는 TEAM 합계 행도 포함하므로 USER 만 골라 필드명을 매핑한다
    // (이전엔 AllocationEntry[] 를 그대로 보내 422 — team 예산 할당 저장이 깨져 있었음).
    const items = allocations
      .filter((e) => e.target_type === 'USER')
      .map((e) => ({ user_id: e.target_id, allocated_usd: e.allocated_usd }));
    await withRetry(() =>
      adminAPI.put(`/admin/budgets/team/${teamId}/allocate`, { allocations: items })
    );
    revalidatePath('/budgets');
    return { success: true, data: undefined };
  } catch (err) {
    return { success: false, error: toErrorMessage(err) };
  }
}

// ─── Team Allocation 조회 ────────────────────────────────────────────────────
// SetBudgetDialog(TEAM)의 share/distribute 모드 판정용 — 멤버 목록과 기존
// 인당 한도(USER scope config)를 읽는다. Decimal 필드는 문자열로 오므로 파싱.

interface RawAllocationEntry {
  target_id: string;
  target_name: string;
  target_type: string;
  target_role?: string | null;
  allocated_usd: string;
  used_usd: string;
  remaining_usd: string;
  alert_level: string;
}

interface RawTeamAllocation {
  team_id: string;
  team_name: string;
  total_budget_usd: string;
  entries: RawAllocationEntry[];
}

export async function getTeamAllocationAction(
  teamId: string
): Promise<ActionResult<TeamBudgetAllocation | null>> {
  try {
    const data = await withRetry(() =>
      adminAPI.get<RawTeamAllocation | null>(`/admin/budgets/team/${teamId}/allocation`)
    );
    if (!data) return { success: true, data: null };
    return {
      success: true,
      data: {
        team_id: data.team_id,
        team_name: data.team_name,
        total_budget_usd: parseFloat(data.total_budget_usd) || 0,
        entries: (data.entries ?? []).map((e) => ({
          target_id: e.target_id,
          target_name: e.target_name,
          target_type: e.target_type.toUpperCase() as AllocationEntry['target_type'],
          target_role: e.target_role ?? null,
          allocated_usd: parseFloat(e.allocated_usd) || 0,
          used_usd: parseFloat(e.used_usd) || 0,
          remaining_usd: parseFloat(e.remaining_usd) || 0,
          alert_level: (e.alert_level ?? 'NORMAL').toUpperCase() as AllocationEntry['alert_level'],
        })),
      },
    };
  } catch (err) {
    return { success: false, error: toErrorMessage(err) };
  }
}

// ─── Downgrade Config ────────────────────────────────────────────────────────

import type { AutoDowngradeConfig } from '@/types/entities';

export async function getDowngradeConfigAction(
  scope: string,
  scopeId: string
): Promise<ActionResult<AutoDowngradeConfig>> {
  try {
    const data = await withRetry(() =>
      adminAPI.get<AutoDowngradeConfig>(
        `/admin/budgets/${scope}/${scopeId}/downgrade`
      )
    );
    return { success: true, data };
  } catch (err) {
    return { success: false, error: toErrorMessage(err) };
  }
}

export async function setDowngradeConfigAction(
  scope: string,
  scopeId: string,
  config: {
    enabled: boolean;
    rules: { from_model_alias: string; to_model_alias: string; threshold_pct: number }[];
  }
): Promise<ActionResult<AutoDowngradeConfig>> {
  try {
    const data = await withRetry(() =>
      adminAPI.put<AutoDowngradeConfig>(
        `/admin/budgets/${scope}/${scopeId}/downgrade`,
        { enabled: config.enabled, rules: config.rules }
      )
    );
    // 다운그레이드 설정은 요약 테이블 데이터와 무관 — revalidatePath 하면
    // RSC 리페치가 테이블을 리마운트시켜 펼침/패널 상태가 날아간다.
    return { success: true, data };
  } catch (err) {
    return { success: false, error: toErrorMessage(err) };
  }
}

export async function deleteDowngradeConfigAction(
  scope: string,
  scopeId: string
): Promise<ActionResult<void>> {
  try {
    await withRetry(() =>
      adminAPI.delete(`/admin/budgets/${scope}/${scopeId}/downgrade`)
    );
    return { success: true, data: undefined };
  } catch (err) {
    return { success: false, error: toErrorMessage(err) };
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function toErrorMessage(err: unknown): string {
  if (err instanceof APIError) {
    return err.message;
  }
  if (err instanceof z.ZodError) {
    return err.issues[0]?.message ?? 'Validation error';
  }
  if (err instanceof Error) {
    return err.message;
  }
  return 'An unexpected error occurred';
}