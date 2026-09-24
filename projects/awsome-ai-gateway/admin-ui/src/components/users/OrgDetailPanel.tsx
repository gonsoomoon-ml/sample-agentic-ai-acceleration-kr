'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.


import { useState, useEffect, useRef, useTransition } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import type { OrgTreeNode, ModelListItem, EffectivePolicy } from '@/types/entities';
import {
  forceReauthTeamAction,
  getUserAllowedClientsAction,
  setUserAllowedClientsAction,
  getEffectivePolicyAction,
  getUserAllowedModelsAction,
  setUserAllowedModelsAction,
  setTeamLeaderAction,
  unsetTeamLeaderAction,
} from '@/lib/actions/users';
import { listActiveModelsAction } from '@/lib/actions/models';
import { CLIENTS, type GatewayClient } from '@/lib/constants/gateway';
import { SpinnerButton } from '@/components/common/SpinnerButton';
import { useToast } from '@/components/common/ToastProvider';
import { Badge, type BadgeTone } from '@/components/common/Badge';
import { TeamModelPermissionPanel, type TeamModelPermissionHandle } from '@/components/users/TeamModelPermissionPanel';
import { ScopeAppAccessPanel, type ScopeAppAccessHandle } from '@/components/users/ScopeAppAccessPanel';
import { EffectivePolicyCard } from '@/components/users/EffectivePolicyCard';
import { BudgetGaugeRow } from '@/components/budgets/budgetVisuals';

interface OrgDetailPanelProps {
  node: OrgTreeNode | null;
}

// Role labels are now i18n-driven — see t('roleLabel.ADMIN') etc.

const ROLE_TONE: Record<string, BadgeTone> = {
  ADMIN: 'pink',
  TEAM_LEADER: 'sky',
  DEVELOPER: 'teal',
};

export function OrgDetailPanel({ node }: OrgDetailPanelProps) {
  const t = useTranslations('users');

  if (!node) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        {t('selectNodePrompt')}
      </div>
    );
  }

  // ── ORGANIZATION ────────────────────────────────────────────────────────────
  if (node.type === 'ORGANIZATION') {
    const deptCount = node.children?.length ?? 0;
    const orgTeamCount = node.meta.team_count ?? null;
    const orgMemberCount = node.meta.member_count ?? null;
    return (
      <div>
        <h2 className="text-lg font-semibold mb-4">{node.name}</h2>
        <div className="flex items-center gap-2 text-sm mb-2">
          <span className="text-muted-foreground">{t('departmentCount')}</span>
          <span className="font-medium">{t('countSuffix', { count: deptCount })}</span>
        </div>
        {orgTeamCount !== null && (
          <div className="flex items-center gap-2 text-sm mb-2">
            <span className="text-muted-foreground">{t('teamCount')}</span>
            <span className="font-medium">{t('countSuffix', { count: orgTeamCount })}</span>
          </div>
        )}
        {orgMemberCount !== null && (
          <div className="flex items-center gap-2 text-sm mb-2">
            <span className="text-muted-foreground">{t('memberCount')}</span>
            <span className="font-medium">{t('memberCountValue', { count: orgMemberCount })}</span>
          </div>
        )}
        <div className="mt-4">
          <ScopeAppAccessPanel scope="organization" scopeId={node.id} />
        </div>
      </div>
    );
  }

  // ── DEPARTMENT ──────────────────────────────────────────────────────────────
  if (node.type === 'DEPARTMENT') {
    // ⚠️ 예전엔 `node.meta.member_count ?? children.length` 를 **팀 수**로 표시했다.
    //    서버는 그 필드에 하위 팀들의 사용자 수 합을 넣으므로, 20팀×50명 부서가
    //    화면에 "팀 1000개" 로 떴다. 지금은 서버가 team_count 를 따로 준다
    //    (admin-api schemas/users.py OrgNodeMeta).
    //
    //    폴백은 children.length 로만 둔다 — 팀 수를 모르면 "모른다" 가 맞고,
    //    사람 수로 대신 채우면 정확히 그 버그가 재발한다.
    const teamCount = node.meta.team_count ?? node.children?.length ?? 0;
    const deptMemberCount = node.meta.member_count ?? null;
    return (
      <div>
        <h2 className="text-lg font-semibold mb-4">{node.name}</h2>
        <div className="flex items-center gap-2 text-sm mb-2">
          <span className="text-muted-foreground">{t('teamCount')}</span>
          <span className="font-medium">{t('countSuffix', { count: teamCount })}</span>
        </div>
        {deptMemberCount !== null && (
          <div className="flex items-center gap-2 text-sm mb-2">
            <span className="text-muted-foreground">{t('memberCount')}</span>
            <span className="font-medium">{t('memberCountValue', { count: deptMemberCount })}</span>
          </div>
        )}
      </div>
    );
  }

  // ── TEAM ────────────────────────────────────────────────────────────────────
  if (node.type === 'TEAM') {
    return <TeamPanel node={node} />;
  }

  // ── USER ────────────────────────────────────────────────────────────────────
  if (node.type === 'USER') {
    return <UserPanel node={node} />;
  }

  return null;
}

// ── USER 상세 (앱 접근 권한 토글 포함) ────────────────────────────────────────

// 앱(client) 집합 — gateway.ts CLIENTS 단일 소스 사용. 새 앱 추가 시 거기만 늘리면
// 토글·예산 입력·dirty 비교가 모두 자동으로 확장된다(이전 both/single 이분법 폐기).
const ALL_CLIENTS = CLIENTS;
type ClientId = GatewayClient;

const CLIENT_OPTIONS: Array<{ value: ClientId; label: string }> = [
  { value: 'claude-code', label: 'Claude Code' },
  { value: 'cowork', label: 'Cowork' },
  { value: 'codex', label: 'Codex' },
];

// API allowed_clients([] = 전체 허용) → UI 체크 상태. [] 면 전부 체크로 표시.
function clientsToSelected(clients: string[]): ClientId[] {
  if (clients.length === 0) return [...ALL_CLIENTS];
  return ALL_CLIENTS.filter((c) => clients.includes(c));
}

// UI 체크 상태 → API allowed_clients. 전부 체크(또는 0개) = 전체 허용 → [](빈 배열).
// 부분 선택만 화이트리스트로 저장. (0개 체크로 잠그는 상태는 허용하지 않음 — 기존 동작 유지.)
function selectedToClients(selected: string[]): string[] {
  const chosen = ALL_CLIENTS.filter((c) => selected.includes(c));
  if (chosen.length === 0 || chosen.length === ALL_CLIENTS.length) return [];
  return chosen;
}

// allowed_clients 비교용 canonical key ([] 와 전체선택을 동일 취급).
function clientsKey(clients: string[]): string {
  return selectedToClients(clients).slice().sort().join(',');
}

function UserPanel({ node }: { node: OrgTreeNode }) {
  const t = useTranslations('users');
  const { toast } = useToast();
  // 로드용/저장용 transition 분리 — 초기 조회 중에 Apply 버튼이 스피너로 보이는 혼동 방지.
  const [isLoadPending, startLoadTransition] = useTransition();
  const [isSavePending, startSaveTransition] = useTransition();

  const email = node.meta.email ?? '-';
  const role = node.meta.role ?? null;
  const teamName = node.meta.team_name ?? t('teamUnassigned');

  const [loadedSelected, setLoadedSelected] = useState<ClientId[]>([...ALL_CLIENTS]);
  const [selected, setSelected] = useState<ClientId[]>([...ALL_CLIENTS]);
  // 허용 클라이언트 정책이 정상 로드됐는지 추적. 로드 실패 시 stale
  // 전체허용([])으로 저장돼 의도치 않게 허용되는 사고를 막기 위해 저장을 건너뛴다.
  const [clientsLoaded, setClientsLoaded] = useState(false);

  // 유효 정책 합성 결과 — 앱별 예산/모델 정책 출처 표시와 EffectivePolicyCard 가
  // 같은 데이터를 쓰므로 한 번만 가져와 공유한다.
  const [policy, setPolicy] = useState<EffectivePolicy | null>(null);

  const toggleClient = (c: ClientId) => {
    setSelected((prev) => (prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]));
  };

  // 사용자별 허용 모델 (팀 정책 override). 비어있음 = override 해제 → 팀 정책 fallback.
  const [models, setModels] = useState<ModelListItem[]>([]);
  const [loadedModelAliases, setLoadedModelAliases] = useState<string[]>([]);
  const [selectedModelAliases, setSelectedModelAliases] = useState<string[]>([]);
  // ★ 모델 정책이 정상 로드됐는지 추적. 로드 실패 시 stale 빈 목록을
  //   저장하면 기존 override 가 의도치 않게 DELETE(팀 폴백)되어 제한이 풀린다 —
  //   국가핵심기술 제한이므로 로드 실패 시에는 모델 정책 저장 자체를 건너뛴다.
  const [modelsLoaded, setModelsLoaded] = useState(false);
  // 개인 override 가 없으면 체크박스에 유효 목록(팀 상속 또는 전체)을 미리 채워
  // 보여준다 — 빈 체크박스는 "전부 차단"으로 읽히기 때문. 미리 채운 값은 표시용
  // 이라 저장 자격이 없어야 하므로, 실제로 쓰는 것은 사용자가 토글한 뒤뿐이다
  // (그대로 저장하면 팀 정책이 개인 override 로 굳어 이후 팀 변경이 안 따라온다).
  const [modelsTouched, setModelsTouched] = useState(false);

  useEffect(() => {
    // 사용자 전환 시 이전 사용자 상태 잔존 방지(잘못된 저장 차단).
    setModelsLoaded(false);
    setClientsLoaded(false);
    setModelsTouched(false);
    setLoadedModelAliases([]);
    setSelectedModelAliases([]);
    startLoadTransition(async () => {
      const [r, p, m, cat] = await Promise.all([
        getUserAllowedClientsAction(node.id),
        getEffectivePolicyAction(node.id),
        getUserAllowedModelsAction(node.id),
        listActiveModelsAction(),
      ]);
      if (r.success) {
        const sel = clientsToSelected(r.data.clients);
        // 개인 정책 행이 없으면 상속된 유효 목록(팀/조직 정책)을 프리필해 보여준다 —
        // userModels 와 같은 규칙: 빈 전체체크는 "제한 없음"으로 오독되므로 실제
        // 적용값을 표시한다. loadedSelected 도 같은 값으로 둬야 프리필이 dirty 로
        // 보이지 않고, 그대로 저장되는 일(= 상속값이 개인 override 로 굳음)도 없다.
        const sel2 =
          r.data.clients.length === 0 &&
          p.success &&
          p.data.allowed_clients_source !== 'user' &&
          p.data.allowed_clients
            ? clientsToSelected(p.data.allowed_clients)
            : sel;
        setLoadedSelected(sel2);
        setSelected(sel2);
        setClientsLoaded(true);
      } else {
        toast({
          type: 'error',
          message: t('loadErrors.appAccess'),
          auto_dismiss_ms: 5000,
        });
      }
      if (p.success) {
        setPolicy(p.data);
      } else {
        setPolicy(null);
      }
      if (cat.success) {
        setModels(cat.data);
      } else {
        toast({
          type: 'error',
          message: t('loadErrors.models'),
          auto_dismiss_ms: 5000,
        });
      }
      if (m.success) {
        setLoadedModelAliases(m.data.modelAliases);
        if (m.data.modelAliases.length > 0) {
          setSelectedModelAliases(m.data.modelAliases);
        } else if (p.success && p.data.allowed_models_source !== 'user') {
          // override 없음 → 유효 목록(팀 정책 또는 전체 모델)을 체크 상태로 표시.
          const inherited =
            p.data.allowed_models ?? (cat.success ? cat.data.map((mm) => mm.alias) : []);
          setSelectedModelAliases(inherited);
        }
        setModelsLoaded(true);
      } else {
        toast({
          type: 'error',
          message: t('loadErrors.userModels'),
          auto_dismiss_ms: 5000,
        });
      }
    });
  }, [node.id]);

  const toggleModel = (alias: string) => {
    setModelsTouched(true);
    setSelectedModelAliases((prev) =>
      prev.includes(alias) ? prev.filter((a) => a !== alias) : [...prev, alias]
    );
  };

  const handleApply = () => {
    startSaveTransition(async () => {
      // ★ 허용 클라이언트 정책이 정상 로드되지 않았으면 stale 전체허용([])을
      //   저장해 의도치 않게 허용되는 사고를 막기 위해 저장 자체를 중단한다.
      if (!clientsLoaded) {
        toast({
          type: 'error',
          message: t('loadErrors.appAccess'),
          auto_dismiss_ms: 4000,
        });
        return;
      }
      // 1) 접근 권한 — 실제로 바뀐 경우만 저장. 상속 프리필을 그대로 저장하면
      //    팀/조직 정책이 개인 override 로 굳어 이후 상위 변경이 안 따라온다.
      let savedSelected = selected;
      if (accessDirty) {
        const r = await setUserAllowedClientsAction(node.id, selectedToClients(selected));
        if (!r.success) {
          toast({ type: 'error', message: r.error, auto_dismiss_ms: 4000 });
          return;
        }
        savedSelected = clientsToSelected(r.data.clients);
      }

      // 2) 사용자별 허용 모델 저장 — 빈 배열이면 action 이 DELETE(override 해제)로 처리.
      // ★ 모델 정책이 정상 로드되지 않았으면(modelsLoaded=false) stale 빈 목록을
      //   저장해 기존 override 를 의도치 않게 해제하는 사고를 막기 위해 저장을 건너뛴다.
      if (modelsLoaded && modelsTouched) {
        const mr = await setUserAllowedModelsAction(node.id, selectedModelAliases);
        if (!mr.success) {
          // 접근 권한은 이미 저장됨 — 모델만 실패. loaded 상태를 동기화 후 알림.
          setLoadedSelected(savedSelected);
          setSelected(savedSelected);
          toast({ type: 'error', message: mr.error, auto_dismiss_ms: 4000 });
          return;
        }
        setLoadedModelAliases(mr.data.modelAliases);
        setSelectedModelAliases(mr.data.modelAliases);
        setModelsTouched(false);
      }

      // 모두 성공 — loaded 상태를 낙관적 값으로 갱신. 유효 정책도 다시 읽어
      // 앱 정책 출처(상속 → 개인 override) 캡션이 즉시 갱신되게 한다.
      setLoadedSelected(savedSelected);
      setSelected(savedSelected);
      const p2 = await getEffectivePolicyAction(node.id);
      if (p2.success) setPolicy(p2.data);
      toast({
        type: 'success',
        message: t('saveSuccess'),
        auto_dismiss_ms: 5000,
      });
    });
  };

  const busy = isLoadPending || isSavePending;

  // 접근 권한·허용 모델 중 하나라도 loaded 상태에서 변경되면 적용 활성화.
  // 접근 권한은 canonical key 로 비교 ([]·전체선택 동일 취급, 순서 무관).
  // clientsLoaded=false 면 stale 상태가 dirty 로 보이지 않게 막는다.
  const accessDirty = clientsLoaded && clientsKey(selected) !== clientsKey(loadedSelected);
  // 모델 선택은 순서 무관 비교 (toggle 시 순서가 바뀌므로).
  // modelsLoaded=false 면 비교 자체를 막아 stale 상태가 dirty 로 보이지 않게 한다.
  // 토글로 실제 변경이 있을 때만 dirty — 상속 프리필은 변경이 아니다.
  const modelsDirty = modelsLoaded && modelsTouched;
  const dirty = accessDirty || modelsDirty;

  const btn = (active: boolean) =>
    [
      'pressable rounded-apple-sm px-3 py-1.5 text-sm font-medium transition-[background,color,box-shadow] duration-150',
      active
        ? 'bg-primary/10 text-primary font-semibold shadow-[inset_0_0_0_1px_hsl(var(--primary)/0.18)]'
        : 'text-muted-foreground interactive',
    ].join(' ');

  return (
    <div>
      <h2 className="text-lg font-semibold mb-4">{node.name}</h2>
      <div className="flex items-center gap-2 text-sm mb-2">
        <span className="text-muted-foreground">{t('email')}</span>
        <span className="font-medium">{email}</span>
      </div>
      <div className="flex items-center gap-2 text-sm mb-2">
        <span className="text-muted-foreground">{t('role')}</span>
        {role ? (
          <Badge tone={ROLE_TONE[role] ?? 'neutral'}>{t(`roleLabel.${role}` as 'roleLabel.ADMIN' | 'roleLabel.TEAM_LEADER' | 'roleLabel.DEVELOPER')}</Badge>
        ) : (
          <span className="font-medium">-</span>
        )}
      </div>
      <div className="flex items-center gap-2 text-sm mb-4">
        <span className="text-muted-foreground">{t('teamNameLabel')}</span>
        <span className="font-medium">{teamName}</span>
      </div>

      <div className="border-t pt-4">
        <div className="flex items-center gap-2 mb-2">
          <p className="text-sm font-medium">{t('appAccess.title')}</p>
          {clientsLoaded && (
            selected.length === ALL_CLIENTS.length ? (
              <span className="badge badge-teal">{t('appAccess.unrestricted')}</span>
            ) : (
              <span className="badge badge-amber">
                {t('appAccess.restricted', { count: selected.length })}
              </span>
            )
          )}
        </div>
        <p className="text-xs text-muted-foreground mb-2">
          {t('appAccess.description')}
        </p>
        {/* 상속 출처 캡션 — 개인 정책이 없을 때 토글 상태는 팀/조직 정책의 프리필이다.
            변경하면 개인 정책으로 저장됨을 명시(userModels 의 상속 캡션과 같은 규칙). */}
        {clientsLoaded &&
          (policy?.allowed_clients_source === 'team' ||
            policy?.allowed_clients_source === 'organization') && (
            <p className="text-xs text-muted-foreground mb-2">
              {policy.allowed_clients_source === 'team'
                ? t('appAccess.inheritTeam')
                : t('appAccess.inheritOrg')}
            </p>
          )}
        {isLoadPending ? (
          <div className="text-xs text-muted-foreground py-1 mb-3">{t('appAccess.loading')}</div>
        ) : (
          <div
            role="group"
            aria-label={t('appAccess.groupLabel')}
            className="glass inline-flex items-center gap-0.5 rounded-apple-md p-1 mb-3"
          >
            {CLIENT_OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                onClick={() => toggleClient(o.value)}
                aria-pressed={selected.includes(o.value)}
                disabled={busy}
                className={btn(selected.includes(o.value))}
              >
                {o.label}
              </button>
            ))}
          </div>
        )}

        {!isLoadPending && (
          <div className="border rounded-apple-md p-3 mb-3">
            <div className="flex items-center justify-between mb-2">
              <p className="text-sm font-medium">{t('budgetInput.title')}</p>
              <Link
                href="/budgets"
                className="text-xs text-primary hover:underline"
              >
                {t('budgetInput.editInBudgets')}
              </Link>
            </div>
            {/* 총예산(사용자/팀) + 앱별 예산을 게이지로 표시. 앱별은 접근 허용과 무관하게
                전체 앱을 보여준다 — 허용되지 않은 앱에 설정된 예산(고아 예산)도
                여기서 보여야 발견할 수 있다. */}
            <div className="space-y-2">
              {policy?.budgets
                .filter((b) => (b.scope === 'USER' && b.client === null) || b.scope === 'TEAM')
                .map((b, i) => (
                  <BudgetGaugeRow
                    key={`total-${i}`}
                    label={b.scope === 'TEAM' ? t('budgetInput.teamTotal') : t('budgetInput.userTotal')}
                    max={b.max_budget_usd}
                    used={b.used_usd}
                    unsetLabel={t('budgetInput.placeholder')}
                  />
                ))}
              {CLIENT_OPTIONS.map((o) => {
                const cfg = policy?.budgets.find(
                  (b) => b.scope === 'USER' && b.client === o.value,
                );
                return (
                  <BudgetGaugeRow
                    key={o.value}
                    label={o.label}
                    max={cfg?.max_budget_usd ?? null}
                    used={cfg?.used_usd ?? null}
                    unsetLabel={t('budgetInput.placeholder')}
                  />
                );
              })}
            </div>
          </div>
        )}

        {!isLoadPending && (
          <div className="border rounded-apple-md p-3 mb-3">
            <p className="text-sm font-medium mb-1">{t('userModels.title')}</p>
            <p className="text-xs text-muted-foreground mb-3">
              {t('userModels.hint')}
            </p>
            {/* 정책 출처 캡션 — 개인 override 가 없으면 체크박스의 체크 상태는
                팀 정책(또는 전체 허용)의 프리필이다. 변경하면 개인 정책으로 저장됨을
                명시한다. */}
            {policy && policy.allowed_models_source !== 'user' && (
              <p className="text-xs text-muted-foreground mb-3">
                {policy.allowed_models_source === 'team'
                  ? t('userModelsInheritTeam')
                  : t('userModelsInheritNone')}
              </p>
            )}
            {!modelsLoaded ? (
              <div className="text-xs text-destructive py-1">
                {t('userModels.loadFailed')}
              </div>
            ) : models.length === 0 ? (
              <div className="text-xs text-muted-foreground py-1">{t('userModels.empty')}</div>
            ) : (
              <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                {models.map((m) => (
                  <label
                    key={m.alias}
                    className="flex items-center gap-2 rounded-apple-sm border p-2 cursor-pointer hover:bg-muted/50"
                  >
                    <input
                      type="checkbox"
                      checked={selectedModelAliases.includes(m.alias)}
                      onChange={() => toggleModel(m.alias)}
                      disabled={busy || !modelsLoaded}
                      className="h-4 w-4 rounded border-gray-300"
                    />
                    <span className="text-sm">{m.display_name || m.alias}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
        )}

        {!isLoadPending && (
          <div className="border rounded-apple-md p-3 mb-3">
            <div className="flex items-center gap-2 mb-1">
              <p className="text-sm font-medium">{t('effectivePolicy.title')}</p>
              <Badge tone="neutral">{t('effectivePolicy.readonly')}</Badge>
            </div>
            <p className="text-xs text-muted-foreground mb-3">
              {t('effectivePolicy.hint')}
            </p>
            <EffectivePolicyCard userId={node.id} policy={policy} models={models} />
          </div>
        )}

        {/* 플로팅 Apply 바 — dirty 일 때만 뜬다. 이 패널은 앱 접근/예산/모델/
            유효 정책을 세로로 길게 쌓는데 버튼이 맨 아래 고정이면 앱 토글 하나
            바꾸고도 끝까지 스크롤해야 했다. 조상에 overflow-hidden 이 있어
            sticky 는 무효라 fixed 로 띄우고, 변경이 생기는 순간 나타나므로
            발견 가능성도 자연히 해결된다. */}
        {dirty && (
          <>
            {/* 플로팅 바 높이만큼 스페이서 — 스크롤 끝에서 마지막 콘텐츠가
                바에 가려지지 않게 한다. */}
            <div className="h-16" aria-hidden="true" />
            <div className="fixed bottom-6 left-1/2 z-40 -translate-x-1/2">
              <div className="flex items-center gap-3 rounded-full border border-border bg-card/95 px-5 py-2.5 shadow-lg backdrop-blur">
                <span className="text-xs font-medium text-muted-foreground whitespace-nowrap">
                  {t('unsavedChanges')}
                </span>
                <SpinnerButton
                  type="button"
                  isLoading={isSavePending}
                  disabled={busy}
                  onClick={handleApply}
                >
                  {t('apply')}
                </SpinnerButton>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── TEAM 상세 (강제 재인증 버튼 포함) ─────────────────────────────────────────

function TeamPanel({ node }: { node: OrgTreeNode }) {
  const t = useTranslations('users');
  const tm = useTranslations('models');
  const tc = useTranslations('common');
  const { toast } = useToast();
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [isLeaderPending, startLeaderTransition] = useTransition();
  const [selectedMemberId, setSelectedMemberId] = useState('');
  const [teamModels, setTeamModels] = useState<ModelListItem[]>([]);
  // 해제할 리더를 확인 모달에서 명확히 지정 — 팀에 리더가 여러 명일 수 있으므로
  // "리더 해제" 버튼 하나로는 어느 사람을 내릴지 알 수 없다.
  const [leaderToRemove, setLeaderToRemove] = useState<{ id: string; name: string } | null>(null);

  // 앱 접근 + 모델 권한 통합 Apply — 두 패널은 자체 버튼을 숨기고(hideActions)
  // dirty/save 를 이 ref·콜백에 맡긴다. UserPanel 의 플로팅 바와 같은 패턴.
  const appAccessRef = useRef<ScopeAppAccessHandle>(null);
  const modelPolicyRef = useRef<TeamModelPermissionHandle>(null);
  const [appDirty, setAppDirty] = useState(false);
  const [modelDirty, setModelDirty] = useState(false);
  const [isPolicySavePending, startPolicySaveTransition] = useTransition();
  const policyDirty = appDirty || modelDirty;

  const handleApplyAll = () => {
    startPolicySaveTransition(async () => {
      // 앱 접근 → 모델 순서로 저장(UserPanel 과 동일). 앞이 실패하면 뒤는 저장하지 않는다.
      if (appDirty && !(await appAccessRef.current?.save())) return;
      if (modelDirty && !(await modelPolicyRef.current?.save())) return;
      toast({ type: 'success', message: t('saveSuccess'), auto_dismiss_ms: 5000 });
    });
  };

  // 팀별 허용 모델 패널용 모델 목록 — 팀 상세가 열릴 때만 로드한다.
  useEffect(() => {
    listActiveModelsAction().then((r) => {
      if (r.success) setTeamModels(r.data);
    });
  }, []);

  const memberCount = node.meta.member_count ?? 0;
  const members = node.children ?? [];
  // 리더 목록은 트리에 이미 실려오는 멤버별 role 로 계산 — 팀 하나에 여러 명일 수 있다
  // (role=TEAM_LEADER 는 팀당 배타적 단일값이 아니라 팀원 각자의 속성).
  const leaders = members.filter((m) => m.meta.role === 'TEAM_LEADER');
  const nonLeaderMembers = members.filter((m) => m.meta.role !== 'TEAM_LEADER');

  const handleForceReauth = () => {
    startTransition(async () => {
      const result = await forceReauthTeamAction(node.id);
      setConfirmOpen(false);
      if (result.success) {
        toast({
          type: 'success',
          message: t('forceReauth.success', { count: result.data.revoked_count }),
          auto_dismiss_ms: 5000,
        });
      } else {
        toast({ type: 'error', message: result.error, auto_dismiss_ms: 4000 });
      }
    });
  };

  const handleAssignLeader = () => {
    if (!selectedMemberId) return;
    startLeaderTransition(async () => {
      const result = await setTeamLeaderAction(selectedMemberId, node.id);
      if (result.success) {
        toast({ type: 'success', message: t('leaderAction.assignSuccess'), auto_dismiss_ms: 4000 });
        setSelectedMemberId('');
        router.refresh();
      } else {
        toast({ type: 'error', message: result.error, auto_dismiss_ms: 4000 });
      }
    });
  };

  const handleConfirmRemoveLeader = () => {
    if (!leaderToRemove) return;
    startLeaderTransition(async () => {
      const result = await unsetTeamLeaderAction(node.id, leaderToRemove.id);
      setLeaderToRemove(null);
      if (result.success) {
        toast({ type: 'success', message: t('leaderAction.unassignSuccess'), auto_dismiss_ms: 4000 });
        router.refresh();
      } else {
        toast({ type: 'error', message: result.error, auto_dismiss_ms: 4000 });
      }
    });
  };

  return (
    <div>
      <h2 className="text-lg font-semibold mb-4">{node.name}</h2>
      <div className="flex items-center gap-2 text-sm mb-4">
        <span className="text-muted-foreground">{t('memberCount')}</span>
        <span className="font-medium">{t('memberCountValue', { count: memberCount })}</span>
      </div>

      <div className="border rounded-apple-md p-3 mb-4">
        <p className="text-sm font-medium mb-2">{t('leaderAction.currentLeaders')}</p>
        {leaders.length === 0 ? (
          <p className="text-xs text-muted-foreground mb-3">{t('leaderAction.noLeaders')}</p>
        ) : (
          <ul className="flex flex-col gap-1.5 mb-3">
            {leaders.map((leaderNode) => (
              <li
                key={leaderNode.id}
                className="flex items-center justify-between gap-2 rounded-apple-sm border px-3 py-1.5"
              >
                <span className="text-sm">{leaderNode.meta.email}</span>
                <button
                  type="button"
                  disabled={isLeaderPending}
                  onClick={() => setLeaderToRemove({ id: leaderNode.id, name: leaderNode.name })}
                  className="text-xs text-destructive hover:underline disabled:opacity-50"
                >
                  {t('leaderAction.unassignButton')}
                </button>
              </li>
            ))}
          </ul>
        )}

        {nonLeaderMembers.length === 0 ? (
          leaders.length === 0 && <p className="text-xs text-muted-foreground">{t('leaderAction.noMembers')}</p>
        ) : (
          <div className="flex items-center gap-2">
            <select
              value={selectedMemberId}
              onChange={(e) => setSelectedMemberId(e.target.value)}
              disabled={isLeaderPending}
              className="glass flex-1 rounded-apple-sm border px-3 py-1.5 text-sm disabled:opacity-50"
            >
              <option value="">{t('leaderAction.selectPlaceholder')}</option>
              {nonLeaderMembers.map((m) => (
                <option key={m.id} value={m.id} disabled={m.meta.role === 'ADMIN'}>
                  {m.meta.email}
                  {m.meta.role === 'ADMIN' ? ' [Admin]' : ''}
                </option>
              ))}
            </select>
            <SpinnerButton
              type="button"
              isLoading={isLeaderPending}
              disabled={!selectedMemberId}
              onClick={handleAssignLeader}
            >
              {t('leaderAction.assignButton')}
            </SpinnerButton>
          </div>
        )}
      </div>

      {leaderToRemove && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-background border rounded-lg shadow-lg max-w-md w-full mx-4 p-6">
            <h3 className="text-base font-semibold mb-3">{t('leaderAction.removeModalTitle')}</h3>
            <p className="text-sm text-muted-foreground mb-2">
              {t('leaderAction.removeModalBody', { name: leaderToRemove.name })}
            </p>
            <p className="text-sm text-muted-foreground mb-4">{t('leaderAction.removeModalNote')}</p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setLeaderToRemove(null)}
                disabled={isLeaderPending}
                className="px-3 py-1.5 text-sm rounded-md border hover:bg-muted"
              >
                {tc('cancel')}
              </button>
              <SpinnerButton
                type="button"
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                isLoading={isLeaderPending}
                onClick={handleConfirmRemoveLeader}
              >
                {t('leaderAction.unassignButton')}
              </SpinnerButton>
            </div>
          </div>
        </div>
      )}

      <div className="mb-4">
        <ScopeAppAccessPanel
          ref={appAccessRef}
          scope="team"
          scopeId={node.id}
          hideActions
          onDirtyChange={setAppDirty}
        />
      </div>

      <div className="mb-4">
        <p className="text-sm font-medium mb-2">{tm('teamModelAccess')}</p>
        <TeamModelPermissionPanel
          ref={modelPolicyRef}
          teamId={node.id}
          models={teamModels}
          hideActions
          onDirtyChange={setModelDirty}
        />
      </div>

      {/* 통합 Apply — 앱 접근/모델 권한 어느 쪽이든 dirty 면 뜬다.
          UserPanel 의 플로팅 바와 같은 패턴: 스크롤 무관하게 항상 보인다. */}
      {policyDirty && (
        <>
          <div className="h-16" aria-hidden="true" />
          <div className="fixed bottom-6 left-1/2 z-40 -translate-x-1/2">
            <div className="flex items-center gap-3 rounded-full border border-border bg-card/95 px-5 py-2.5 shadow-lg backdrop-blur">
              <span className="text-xs font-medium text-muted-foreground whitespace-nowrap">
                {t('unsavedChanges')}
              </span>
              <SpinnerButton
                type="button"
                isLoading={isPolicySavePending}
                disabled={isPolicySavePending}
                onClick={handleApplyAll}
              >
                {t('apply')}
              </SpinnerButton>
            </div>
          </div>
        </>
      )}

      <SpinnerButton
        type="button"
        className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
        onClick={() => setConfirmOpen(true)}
        disabled={memberCount === 0}
      >
        {t('forceReauth.button')}
      </SpinnerButton>

      {confirmOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-background border rounded-lg shadow-lg max-w-md w-full mx-4 p-6">
            <h3 className="text-base font-semibold mb-3">{t('forceReauth.button')}</h3>
            <p className="text-sm text-muted-foreground mb-2">
              <span className="font-medium text-foreground">{node.name}</span>{' '}
              {t('forceReauth.warning', { count: memberCount })}
            </p>
            <p className="text-sm text-muted-foreground mb-4">
              {t('forceReauth.note')}
            </p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmOpen(false)}
                disabled={isPending}
                className="px-3 py-1.5 text-sm rounded-md border hover:bg-muted"
              >
                {tc('cancel')}
              </button>
              <SpinnerButton
                type="button"
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                isLoading={isPending}
                onClick={handleForceReauth}
              >
                {t('forceReauth.proceed')}
              </SpinnerButton>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}