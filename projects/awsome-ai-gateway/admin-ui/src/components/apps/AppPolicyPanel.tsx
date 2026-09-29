'use client';
// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

import { useState, useTransition, useEffect } from 'react';
import { useRouter, usePathname, useSearchParams } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { getAppPolicyAction, setAppDefaultModelAction, toggleAppModelAction } from '@/lib/actions/apps';
import { setClientWebSearchAction } from '@/lib/actions/routing';
import type { AppPolicy, AppModelRef } from '@/lib/actions/apps';
import { ConfirmDialog } from '@/components/common/ConfirmDialog';
import { InfoTooltip } from '@/components/common/InfoTooltip';
import { SpinnerButton } from '@/components/common/SpinnerButton';
import { FormError } from '@/components/common/FormError';
import { Table, THead, TBody, Tr, Th, Td, TEmpty } from '@/components/common/Table';
import { SkeletonCard } from '@/components/common/SkeletonCard';
import { useToast } from '@/components/common/ToastProvider';
import {
  CLIENTS as GATEWAY_CLIENTS,
  CLIENT_LABELS,
  CLIENT_SHORT_LABELS,
  modelAllowsClient,
  modelAppScope,
} from '@/lib/constants/gateway';

// 이 패널이 routing_profiles.default_model 과 앱별 모델 허용을 바꿀 수 있는 유일한
// 화면이다. 목록에 앱이 빠지면 그 앱의 default_model 을 콘솔에서 전혀 설정할 수 없고,
// gateway 는 프로필의 default_model 로 라우팅하므로 사실상 그 앱이 동작하지 않는다.
const CLIENTS = GATEWAY_CLIENTS.map((value) => ({
  value: value as string,
  label: CLIENT_LABELS[value] ?? value,
}));

const APP_PARAM = 'app';

export function AppPolicyPanel() {
  const t = useTranslations('apps');
  const tc = useTranslations('common');
  const { toast } = useToast();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  // 선택 앱은 ?app= 쿼리가 정본이다 — useState 로 두면 mutation action 의 revalidatePath
  // 가 일으키는 RSC 리페치/리마운트 때 선택이 날아간다(실제로 그 버그가 있었다).
  // URL 에 두면 리마운트·새로고침·딥링크 모두에서 선택이 유지된다.
  // (APP_PARAM 상수: 쿼리명을 리터럴로 쓰면 i18n 키 스캔 테스트가 t() 호출로 오인한다.)
  // 쿼리가 없거나 이상하면 첫 앱(claude-code)을 기본 선택 — 빈 상태 문구를 띄우지 않는다.
  const rawClient = searchParams.get(APP_PARAM) ?? '';
  const selectedClient = CLIENTS.some((c) => c.value === rawClient)
    ? rawClient
    : (CLIENTS[0]?.value ?? '');
  const [policy, setPolicy] = useState<AppPolicy | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isLoadPending, startLoadTransition] = useTransition();
  const [defaultModelInput, setDefaultModelInput] = useState('');
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isSavePending, startSaveTransition] = useTransition();
  // kind 는 어느 확인 문구를 띄울지 고른다. 'all' = NULL(전체 허용) 을 명시 목록으로 굳히는
  // 전이, 'last' = 마지막 허용 앱을 빼서 `[]`(전면 거부) 로 가는 전이. 문구를 하나로 합치면
  // 둘 중 하나는 반드시 거짓말이 된다(전자는 "범위가 바뀔 수 있다", 후자는 "아무 앱도 못 쓴다").
  const [pendingToggle, setPendingToggle] = useState<
    { alias: string; allowed: boolean; kind: 'all' | 'last' } | null
  >(null);
  const [isWsPending, startWsTransition] = useTransition();

  // 선택 변경 → URL 갱신. policy fetch 는 아래 useEffect 가 selectedClient 를 보고 수행한다
  // (클릭 경로뿐 아니라 리마운트 복구 경로에서도 같은 코드를 타게 하기 위함).
  useEffect(() => {
    setPolicy(null);
    setLoadError(null);
    setDefaultModelInput('');
    setSaveError(null);
    if (!selectedClient) return;
    let cancelled = false;
    startLoadTransition(async () => {
      const result = await getAppPolicyAction(selectedClient);
      if (cancelled) return;
      if (result.success) {
        setPolicy(result.data);
        setDefaultModelInput(result.data.default_model ?? '');
      } else {
        setLoadError(result.error);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [selectedClient]);

  const handleClientChange = (client: string) => {
    if (client === selectedClient) return;
    const sp = new URLSearchParams(searchParams.toString());
    if (client) sp.set(APP_PARAM, client);
    else sp.delete(APP_PARAM);
    const qs = sp.toString();
    router.push(qs ? `${pathname}?${qs}` : pathname);
  };

  const handleToggleWebSearch = (enabled: boolean) => {
    if (!policy) return;
    startWsTransition(async () => {
      const result = await setClientWebSearchAction(selectedClient, enabled);
      if (result.success) {
        setPolicy((prev) => (prev ? { ...prev, web_search_enabled: enabled } : prev));
        toast({ type: 'success', message: t('webSearchSaved'), auto_dismiss_ms: 3000 });
      } else {
        toast({ type: 'error', message: result.error, auto_dismiss_ms: 4000 });
      }
    });
  };

  const handleSaveDefaultModel = () => {
    setSaveError(null);
    startSaveTransition(async () => {
      const result = await setAppDefaultModelAction(selectedClient, defaultModelInput.trim());
      if (result.success) {
        toast({ type: 'success', message: t('defaultModelSaved'), auto_dismiss_ms: 3000 });
        setPolicy((prev) => (prev ? { ...prev, default_model: defaultModelInput.trim() } : prev));
      } else {
        setSaveError(result.error);
      }
    });
  };

  // MODEL 축(model_aliases.allowed_clients): null = 제한 없음 / [] = 허용 앱 없음 / 목록 = 그 앱만.
  // 판정은 손으로 쓰지 않고 `modelAllowsClient` 를 지난다 — 예전엔 여기서 `=== null || includes`
  // 를 직접 썼고(맞는 판정이었지만) 같은 판정이 세 파일에 흩어져 있어서, 그중 하나만
  // `!m.allowed_clients` 로 "간단히" 바뀌는 순간 `[]`(전면 거부) 가 전 앱 허용으로 보인다.
  // 정본은 admin-api 의 조회 SQL(`allowed_clients IS NULL OR :client = ANY(allowed_clients)`,
  // routers/apps.py) 과 게이트웨이의 `check_client_model_scope` 다.
  const isAllowed = (m: AppModelRef) => modelAllowsClient(m.allowed_clients, selectedClient);

  /** 이 앱을 빼면 허용 앱이 하나도 남지 않는가 = 이 클릭이 전면 거부를 만드는가. */
  const isLastAllowedApp = (m: AppModelRef) => {
    // NULL(제한 없음) 에서는 절대 참이 아니다 — 서버가 전체 목록으로 materialize 한 뒤 이 앱만
    // 빼므로 다른 앱이 남는다(`_next_allowed_clients`). 그래서 'list' 만 본다.
    const scope = modelAppScope(m.allowed_clients);
    return scope.kind === 'list' && scope.clients.every((c) => c === selectedClient);
  };

  const doToggle = (alias: string, allowed: boolean) => {
    startSaveTransition(async () => {
      const result = await toggleAppModelAction(selectedClient, alias, allowed);
      if (result.success) {
        setPolicy(result.data);
        toast({ type: 'success', message: t('toggleSaved'), auto_dismiss_ms: 2500 });
      } else {
        toast({ type: 'error', message: result.error, auto_dismiss_ms: 4000 });
      }
    });
  };

  const handleToggleClick = (m: AppModelRef, nextAllowed: boolean) => {
    // 해제 클릭 하나가 두 가지 서로 다른 사고를 만들 수 있어서 확인 문구를 갈라 쓴다.
    // 서버가 다음 값을 계산하므로(admin-api `routers/apps.py::_next_allowed_clients`)
    // 여기서 배열을 만들지는 않고, "무엇이 될지" 만 판정한다:
    //
    //  (1) NULL 해제 → sorted(VALID_CLIENTS) 에서 이 앱만 뺀 **명시 목록** 으로 굳는다.
    //      이후 추가되는 앱은 그 목록에 없으므로 자동으로 거부된다 = 다른 앱의 미래 범위 변화.
    //  (2) 마지막 남은 앱 해제 → `[]` = 전면 거부. 게이트웨이가
    //      `check_client_model_scope` 를 fail-closed 로 바꾼 뒤(`if allowed is None: return`)
    //      이 값은 모든 앱에서 모델을 막는다. 이 화면이 전면 거부를 만드는 유일한 원클릭
    //      경로인데 예전엔 확인도, 구별되는 토스트도 없었다(성공 토스트 하나뿐).
    if (!nextAllowed && m.allowed_clients === null) {
      setPendingToggle({ alias: m.alias, allowed: false, kind: 'all' });
      return;
    }
    if (!nextAllowed && isLastAllowedApp(m)) {
      setPendingToggle({ alias: m.alias, allowed: false, kind: 'last' });
      return;
    }
    doToggle(m.alias, nextAllowed);
  };

  return (
    <div className="space-y-6">
      <div className="glass rounded-apple p-4">
        <p className="text-xs font-medium text-muted-foreground mb-2">{t('selectApp')}</p>
        <div
          role="group"
          aria-label={t('selectApp')}
          className="glass inline-flex items-center gap-0.5 rounded-apple-md p-1"
        >
          {CLIENTS.map(({ value, label }) => (
            <button
              key={value}
              type="button"
              onClick={() => handleClientChange(value)}
              aria-pressed={selectedClient === value}
              className={[
                'pressable rounded-apple-sm px-3 py-1.5 text-sm font-medium transition-[background,color,box-shadow] duration-150',
                selectedClient === value
                  ? 'bg-primary/10 text-primary font-semibold shadow-[inset_0_0_0_1px_hsl(var(--primary)/0.18)]'
                  : 'text-muted-foreground interactive',
              ].join(' ')}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {!selectedClient && !isLoadPending && (
        <div className="glass rounded-apple p-4">
          <p className="text-sm text-muted-foreground">{t('selectPrompt')}</p>
        </div>
      )}
      {isLoadPending && <SkeletonCard count={3} />}
      {loadError && <FormError error={loadError} />}

      {policy && !isLoadPending && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="glass glass-hover rounded-apple p-4 flex flex-col gap-2">
              <p className="text-sm font-medium text-muted-foreground">{t('allowedModels')}</p>
              <p className="text-2xl font-bold num">{policy.allowed_models.length}</p>
            </div>
            <div className="glass glass-hover rounded-apple p-4 flex flex-col gap-2">
              <p className="text-sm font-medium text-muted-foreground">{t('allowedUsers')}</p>
              <p className="text-2xl font-bold num">{policy.allowed_users.length}</p>
            </div>
            <div className="glass glass-hover rounded-apple p-4 flex flex-col gap-2">
              <p className="text-sm font-medium text-muted-foreground">{t('defaultModel')}</p>
              <p className="text-sm font-mono mono-id truncate">
                {policy.default_model ?? (
                  <span className="text-muted-foreground font-sans">{t('notSet')}</span>
                )}
              </p>
            </div>
          </div>

          <div className="glass rounded-apple p-4 space-y-3">
            <h2 className="text-sm font-semibold flex items-center gap-1">
              {t('defaultModel')}
              <InfoTooltip label={t('defaultModel')} side="top">{t('defaultModelHint')}</InfoTooltip>
            </h2>
            <div className="flex items-center gap-3">
              <select
                value={defaultModelInput}
                onChange={(e) => setDefaultModelInput(e.target.value)}
                disabled={policy.allowed_models.length === 0}
                className="flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
              >
                <option value="">{t('selectPlaceholder')}</option>
                {policy.allowed_models.map((alias) => (
                  <option key={alias} value={alias}>{alias}</option>
                ))}
              </select>
              <SpinnerButton
                type="button"
                isLoading={isSavePending}
                onClick={handleSaveDefaultModel}
                disabled={!defaultModelInput || defaultModelInput === policy.default_model}
              >
                {tc('save')}
              </SpinnerButton>
            </div>
            {saveError && <FormError error={saveError} />}
          </div>

          <div className="glass rounded-apple p-4 flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold">{t('webSearchTitle')}</h2>
              <p className="text-xs text-muted-foreground mt-0.5">{t('webSearchHint')}</p>
            </div>
            <label className="flex items-center gap-2 cursor-pointer">
              <div className="relative inline-flex items-center">
                <input
                  type="checkbox"
                  checked={policy.web_search_enabled}
                  onChange={(e) => handleToggleWebSearch(e.target.checked)}
                  disabled={isWsPending}
                  className="sr-only peer"
                  aria-label={t('webSearchTitle')}
                />
                <div className="w-9 h-5 bg-muted-foreground/30 peer-checked:bg-primary rounded-full transition-colors after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:w-4 after:h-4 after:bg-background after:rounded-full after:transition-transform peer-checked:after:translate-x-4" />
              </div>
              <span className="text-sm">
                {policy.web_search_enabled ? t('webSearchOn') : t('webSearchOff')}
              </span>
            </label>
          </div>

          <div className="space-y-3">
            <h2 className="text-sm font-semibold">{t('modelManagement')}</h2>
            <div className="glass rounded-apple overflow-hidden">
              {/* 모델 수만큼 길어지는 표 — max-h + overflow-y 로 스크롤시키고
                  헤더는 sticky 로 고정(bg-card 로 아래 행을 가림). */}
              <Table wrapperClassName="max-h-96 overflow-y-auto">
                <THead className="[&_th]:sticky [&_th]:top-0 [&_th]:z-10 [&_th]:bg-card">
                  <Tr>
                    <Th>{t('colAlias')}</Th>
                    <Th>{t('colAllowAll')}</Th>
                    <Th>{t('colAllowThisApp')}</Th>
                  </Tr>
                </THead>
                <TBody>
                  {policy.all_models.length === 0 ? (
                    <TEmpty colSpan={3}>{t('noModels')}</TEmpty>
                  ) : (
                    policy.all_models.map((m) => (
                      <Tr key={m.alias}>
                        <Td emphasis className="font-mono mono-id text-xs">{m.alias}</Td>
                        <Td>
                          {/* 앱 상태 = 앱별 고정 칩 3개 — 켜짐(teal)/꺼짐(dim)으로 커버리지를
                              한눈에 읽게 한다. 예전 표시(전체 배지/전면 거부 배지/— 대시)는
                              부분 허용의 "어떤 앱인지" 를 숨기고 세 상태의 형식이 달라
                              행 비교가 안 됐다.
                              NULL(전체 허용) 은 세 칩이 모두 켜진 것과 같지만 "향후 추가 앱도
                              포함" 이라는 별도 의미가 있으므로 title/sr-only 로 구분한다. */}
                          {(() => {
                            const scope = modelAppScope(m.allowed_clients);
                            const hint =
                              scope.kind === 'unrestricted'
                                ? t('appStatusUnrestrictedHint')
                                : undefined;
                            return (
                              <span
                                className="inline-flex items-center gap-1"
                                title={hint}
                              >
                                {GATEWAY_CLIENTS.map((c) => {
                                  const on = modelAllowsClient(m.allowed_clients, c);
                                  return (
                                    <span
                                      key={c}
                                      className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${
                                        on
                                          ? 'bg-teal-500/15 text-teal-700 dark:text-teal-300'
                                          : 'bg-muted/50 text-muted-foreground/50 line-through'
                                      }`}
                                    >
                                      {CLIENT_SHORT_LABELS[c]}
                                    </span>
                                  );
                                })}
                                {hint && <span className="sr-only">{hint}</span>}
                              </span>
                            );
                          })()}
                        </Td>
                        <Td>
                          <input
                            type="checkbox"
                            className="h-4 w-4"
                            checked={isAllowed(m)}
                            disabled={isSavePending}
                            onChange={(e) => handleToggleClick(m, e.target.checked)}
                          />
                        </Td>
                      </Tr>
                    ))
                  )}
                </TBody>
              </Table>
            </div>
          </div>

          <div className="space-y-3">
            <h2 className="text-sm font-semibold">{t('allowedUsers')}</h2>
            <div className="glass rounded-apple overflow-hidden">
              {/* 유저 수만큼 길어진다 — 모델 표와 같은 스크롤+sticky 헤더. */}
              <Table wrapperClassName="max-h-96 overflow-y-auto">
                <THead className="[&_th]:sticky [&_th]:top-0 [&_th]:z-10 [&_th]:bg-card">
                  <Tr><Th>{t('colUser')}</Th><Th>{t('colAccess')}</Th><Th>ID</Th></Tr>
                </THead>
                <TBody>
                  {policy.allowed_users.length === 0 ? (
                    <TEmpty colSpan={3}>{t('noAllowedUsers')}</TEmpty>
                  ) : (
                    policy.allowed_users.map((u) => (
                      <Tr key={u.user_id}>
                        <Td emphasis>
                          {u.email ?? <span className="text-muted-foreground">{t('noEmail')}</span>}
                        </Td>
                        <Td>
                          <span
                            className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${
                              u.explicit
                                ? 'bg-teal-500/15 text-teal-700 dark:text-teal-300'
                                : 'bg-muted text-muted-foreground'
                            }`}
                          >
                            {u.explicit ? t('accessExplicit') : t('accessUnrestricted')}
                          </span>
                        </Td>
                        <Td className="font-mono mono-id text-xs text-muted-foreground">{u.user_id}</Td>
                      </Tr>
                    ))
                  )}
                </TBody>
              </Table>
            </div>
          </div>
        </div>
      )}
      <ConfirmDialog
        isOpen={pendingToggle !== null}
        onClose={() => setPendingToggle(null)}
        onConfirm={() => {
          if (pendingToggle) doToggle(pendingToggle.alias, pendingToggle.allowed);
          setPendingToggle(null);
        }}
        title={pendingToggle?.kind === 'last' ? t('confirmLastTitle') : t('confirmAllTitle')}
        message={
          pendingToggle?.kind === 'last'
            ? t('confirmLastMessage', { alias: pendingToggle.alias })
            : t('confirmAllMessage')
        }
        confirmLabel={t('confirmAllLabel')}
        isDestructive
      />
    </div>
  );
}
