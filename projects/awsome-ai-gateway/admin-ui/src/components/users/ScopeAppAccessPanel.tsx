'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

// 팀/조직 단위 앱 접근 정책 패널 (alembic 0038).
// 우선순위 user > team > org > 제한없음 — 여기서 설정한 목록은 개인 override 가
// 없는 멤버에게 적용된다. 모델 정책의 team_allowed_models 와 같은 상속 구조.
//
// hideActions=true 로 임베드하면 자체 Apply 를 숨기고 ref.save() / onDirtyChange 를
// 부모의 통합 저장(OrgDetailPanel TeamPanel 의 플로팅 Apply 바)에 맡긴다.

import { forwardRef, useImperativeHandle, useState, useEffect, useTransition } from 'react';
import { useTranslations } from 'next-intl';
import {
  getScopeAllowedClientsAction,
  setScopeAllowedClientsAction,
} from '@/lib/actions/users';
import { CLIENTS, CLIENT_LABELS, type GatewayClient } from '@/lib/constants/gateway';
import { SpinnerButton } from '@/components/common/SpinnerButton';
import { useToast } from '@/components/common/ToastProvider';

const ALL_CLIENTS = CLIENTS;
type ClientId = GatewayClient;

// API rows → 체크 상태. [] = 정책 없음 = 전부 체크로 표시(제한없음과 동일 의미).
function clientsToSelected(clients: string[]): ClientId[] {
  if (clients.length === 0) return [...ALL_CLIENTS];
  return ALL_CLIENTS.filter((c) => clients.includes(c));
}

// 체크 상태 → API rows. 전부 체크(또는 0개) = 정책 없음 → [].
// 부분 선택만 화이트리스트로 저장 — 0개 체크로 "전면 거부" 는 만들 수 없다
// (정책 없음과 구분 불가한 표현이라 의도적으로 지원하지 않음).
function selectedToClients(selected: string[]): string[] {
  const chosen = ALL_CLIENTS.filter((c) => selected.includes(c));
  if (chosen.length === 0 || chosen.length === ALL_CLIENTS.length) return [];
  return chosen;
}

export interface ScopeAppAccessHandle {
  /** 통합 저장 경로 — 성공 시 true. 실패 시 토스트를 띄우고 false. */
  save: () => Promise<boolean>;
}

interface ScopeAppAccessPanelProps {
  scope: 'team' | 'organization';
  scopeId: string;
  /** 자체 Apply 버튼 숨김 — 부모의 통합 저장이 ref.save() 를 호출한다. */
  hideActions?: boolean;
  onDirtyChange?: (_dirty: boolean) => void;
}

export const ScopeAppAccessPanel = forwardRef<ScopeAppAccessHandle, ScopeAppAccessPanelProps>(
  function ScopeAppAccessPanel({ scope, scopeId, hideActions, onDirtyChange }, ref) {
    const t = useTranslations('users.scopeAppAccess');
    const { toast } = useToast();
    const [isLoadPending, startLoadTransition] = useTransition();
    const [isSavePending, startSaveTransition] = useTransition();

    const [loadedSelected, setLoadedSelected] = useState<ClientId[]>([...ALL_CLIENTS]);
    const [selected, setSelected] = useState<ClientId[]>([...ALL_CLIENTS]);
    // 정책이 정상 로드됐는지 추적 — 로드 실패 상태에서 저장하면 stale 전체허용이
    // 기존 제한을 덮어쓸 수 있으므로 저장을 차단한다(UserPanel 과 같은 규칙).
    const [loaded, setLoaded] = useState(false);

    useEffect(() => {
      setLoaded(false);
      startLoadTransition(async () => {
        const r = await getScopeAllowedClientsAction(scope, scopeId);
        if (r.success) {
          const sel = clientsToSelected(r.data.clients);
          setLoadedSelected(sel);
          setSelected(sel);
          setLoaded(true);
        } else {
          toast({ type: 'error', message: t('loadError'), auto_dismiss_ms: 5000 });
        }
      });
    }, [scope, scopeId]);

    const toggle = (c: ClientId) => {
      setSelected((prev) =>
        prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c],
      );
    };

    const busy = isLoadPending || isSavePending;
    const dirty =
      loaded &&
      selectedToClients(selected).join(',') !==
        selectedToClients(loadedSelected).join(',');

    useEffect(() => {
      onDirtyChange?.(dirty);
    }, [dirty, onDirtyChange]);

    const save = async (): Promise<boolean> => {
      if (!loaded) return false;
      const r = await setScopeAllowedClientsAction(
        scope,
        scopeId,
        selectedToClients(selected),
      );
      if (!r.success) {
        toast({ type: 'error', message: r.error, auto_dismiss_ms: 4000 });
        return false;
      }
      const sel = clientsToSelected(r.data.clients);
      setLoadedSelected(sel);
      setSelected(sel);
      if (!hideActions) {
        toast({ type: 'success', message: t('saveSuccess'), auto_dismiss_ms: 4000 });
      }
      return true;
    };

    useImperativeHandle(ref, () => ({ save }));

    const handleApply = () => {
      startSaveTransition(async () => {
        await save();
      });
    };

    const btn = (active: boolean) =>
      [
        'pressable rounded-apple-sm px-3 py-1.5 text-sm font-medium transition-[background,color,box-shadow] duration-150',
        active
          ? 'bg-primary/10 text-primary font-semibold shadow-[inset_0_0_0_1px_hsl(var(--primary)/0.18)]'
          : 'text-muted-foreground interactive',
      ].join(' ');

    return (
      <div className="border rounded-apple-md p-3">
        <div className="flex items-center gap-2 mb-1">
          <p className="text-sm font-medium">{t('title')}</p>
          {loaded &&
            (selected.length === ALL_CLIENTS.length ? (
              <span className="badge badge-teal">{t('unrestricted')}</span>
            ) : (
              <span className="badge badge-amber">
                {t('restricted', { count: selected.length })}
              </span>
            ))}
        </div>
        <p className="text-xs text-muted-foreground mb-3">
          {scope === 'team' ? t('descriptionTeam') : t('descriptionOrg')}
        </p>
        {isLoadPending ? (
          <div className="text-xs text-muted-foreground py-1">{t('loading')}</div>
        ) : (
          <div className="flex items-center gap-3 flex-wrap">
            <div
              role="group"
              aria-label={t('groupLabel')}
              className="glass inline-flex items-center gap-0.5 rounded-apple-md p-1"
            >
              {ALL_CLIENTS.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => toggle(c)}
                  aria-pressed={selected.includes(c)}
                  disabled={busy}
                  className={btn(selected.includes(c))}
                >
                  {CLIENT_LABELS[c]}
                </button>
              ))}
            </div>
            {!hideActions && dirty && (
              <SpinnerButton
                type="button"
                isLoading={isSavePending}
                disabled={busy}
                onClick={handleApply}
              >
                {t('apply')}
              </SpinnerButton>
            )}
          </div>
        )}
      </div>
    );
  },
);
