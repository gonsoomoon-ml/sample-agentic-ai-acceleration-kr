'use server';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

import { revalidatePath } from 'next/cache';
import { getTranslations } from 'next-intl/server';
import { z } from 'zod';
import { adminAPI } from '@/lib/api-client';
import { withRetry } from '@/lib/utils/retry';
import { APIError } from '@/lib/utils/retry';
import type { ActionResult } from './types';

// ─── Types ────────────────────────────────────────────────────────────────────

export interface AppUserRef {
  user_id: string;
  /** false = 명시 행이 없는 사용자 — 앱 제한 없음(fail-open)으로 허용에 포함. */
  explicit: boolean;
  email?: string | null;
}

export interface AppModelRef {
  alias: string;
  // admin-api `routers/apps.py` 의 AppModelRef 미러. MODEL 축 3-state 를 그대로 받는다
  // (asyncpg 가 PG '{}' 를 [] 로 준다 — 접히지 않는다):
  //   null = 제한 없음 / [] = 허용 앱 없음(전면 거부) / 목록 = 그 앱만.
  // `[]` 행은 GET /admin/apps/{client}.allowed_models 에서는 빠지고 all_models 에만 남는다.
  allowed_clients: string[] | null;
}

export interface AppPolicy {
  client: string;
  allowed_models: string[];
  default_model: string | null;
  allowed_users: AppUserRef[];
  all_models: AppModelRef[];
  web_search_enabled: boolean;
}

// ─── getAppPolicyAction ───────────────────────────────────────────────────────

export async function getAppPolicyAction(client: string): Promise<ActionResult<AppPolicy>> {
  try {
    const data = await withRetry(() =>
      adminAPI.get<AppPolicy>(`/admin/apps/${encodeURIComponent(client)}`)
    );
    return { success: true, data };
  } catch (err) {
    return { success: false, error: toErrorMessage(err) };
  }
}

// ─── setAppDefaultModelAction ─────────────────────────────────────────────────

export async function setAppDefaultModelAction(
  client: string,
  default_model: string
): Promise<ActionResult<void>> {
  const t = await getTranslations('errors');
  if (!client) return { success: false, error: t('appRequired') };
  if (!default_model) return { success: false, error: t('defaultModelRequired') };

  try {
    await withRetry(() =>
      adminAPI.patch(`/admin/apps/${encodeURIComponent(client)}/default-model`, { default_model })
    );
    // revalidatePath('/apps') 는 의도적으로 하지 않는다 — /apps 페이지는 서버 fetch 데이터가
    // 없고(정책은 클라이언트가 action 으로 읽음), 현재 라우트 RSC 리페치만 일으켜
    // 패널 리마운트(선택 앱 유실)의 원인이 됐다.
    return { success: true, data: undefined };
  } catch (err) {
    return { success: false, error: toErrorMessage(err) };
  }
}

// ─── toggleAppModelAction ─────────────────────────────────────────────────────

export async function toggleAppModelAction(
  client: string,
  alias: string,
  allowed: boolean
): Promise<ActionResult<AppPolicy>> {
  const t = await getTranslations('errors');
  if (!client) return { success: false, error: t('appRequired') };
  try {
    const data = await withRetry(() =>
      adminAPI.patch<AppPolicy>(
        `/admin/apps/${encodeURIComponent(client)}/models`,
        { alias, allowed }
      )
    );
    // '/apps' 는 무효화하지 않는다(위 setAppDefaultModelAction 주석 참조 — 서버 데이터 없음,
    // 리페치→리마운트로 선택 앱이 날아가는 버그의 원인이었다).
    // 이 PATCH 는 `model_aliases.allowed_clients`(MODEL 축) 를 바꾼다 — 즉 /models 의 "허용 앱"
    // 열과 편집 다이얼로그의 prefill 도 함께 낡는다. '/apps' 만 무효화하면 다른 탭의 /models
    // 스냅샷이 옛 정책을 계속 보여주고, 운영자는 그 화면을 보고 쓰기 판단을 한다.
    // (다이얼로그가 안 건드린 정책 값을 아예 보내지 않게 된 뒤에도 이 무효화는 필요하다:
    //  덮어쓰기는 막히지만 "화면에 보이는 값이 사실과 다르다" 는 그대로 남는다.)
    revalidatePath('/models');
    return { success: true, data };
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
