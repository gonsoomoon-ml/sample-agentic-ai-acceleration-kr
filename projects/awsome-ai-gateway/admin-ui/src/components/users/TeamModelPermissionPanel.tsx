'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.


import { forwardRef, useImperativeHandle, useState, useTransition, useEffect } from 'react';
import { useTranslations } from 'next-intl';
import { useToast } from '@/components/common/ToastProvider';
import { SpinnerButton } from '@/components/common/SpinnerButton';
import {
  getTeamAllowedModelsAction,
  setTeamAllowedModelsAction,
  clearTeamAllowedModelsAction,
} from '@/lib/actions/models';
import type { ModelListItem } from '@/types/entities';

interface TeamOption {
  id: string;
  name: string;
  // 팀명이 부서 간 중복될 수 있어(예: 여러 부서에 "Developers" 팀), 드롭다운
  // 표시에 부서명을 병기해 구분한다.
  department_name: string | null;
}

export interface TeamModelPermissionHandle {
  /** 통합 저장 경로 — 성공 시 true. 실패 시 토스트를 띄우고 false. */
  save: () => Promise<boolean>;
}

interface TeamModelPermissionPanelProps {
  // 팀 상세(OrgDetailPanel)에 임베드될 때는 teamId 를 직접 받고 드롭다운을 숨긴다.
  // 미제공 시 기존 동작 — 드롭다운으로 팀을 고른다.
  teamId?: string;
  teams?: TeamOption[];
  allTeams?: TeamOption[];
  models: ModelListItem[];
  /** 자체 저장 버튼 숨김 — 부모의 통합 저장이 ref.save() 를 호출한다.
      (제한 해제 링크는 별도 동작이라 hideActions 에서도 유지) */
  hideActions?: boolean;
  onDirtyChange?: (_dirty: boolean) => void;
}

export const TeamModelPermissionPanel = forwardRef<TeamModelPermissionHandle, TeamModelPermissionPanelProps>(
  function TeamModelPermissionPanel({ teamId, teams = [], allTeams, models, hideActions, onDirtyChange }, ref) {
  const t = useTranslations('models');
  const { toast } = useToast();
  const [isPending, startTransition] = useTransition();
  const [pickedTeamId, setPickedTeamId] = useState('');
  const selectedTeamId = teamId ?? pickedTeamId;
  const [allowedAliases, setAllowedAliases] = useState<string[]>([]);
  // 로드 시점의 저장값 — dirty 비교 기준. 로드 실패/미로드 시 저장 자체를 막는다
  // (stale 상태를 저장해 기존 정책을 덮어쓰는 사고 방지 — UserPanel 과 같은 규칙).
  const [loadedAliases, setLoadedAliases] = useState<string[]>([]);
  const [hasRestrictions, setHasRestrictions] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [showInactive, setShowInactive] = useState(false);

  const visibleTeams = showInactive && allTeams ? allTeams : teams;

  const activeModels = models.filter(m => m.is_active);

  useEffect(() => {
    if (!selectedTeamId) {
      setAllowedAliases([]);
      setLoadedAliases([]);
      setHasRestrictions(false);
      setLoaded(false);
      return;
    }
    startTransition(async () => {
      const result = await getTeamAllowedModelsAction(selectedTeamId);
      if (result.success) {
        setAllowedAliases(result.data.model_aliases);
        setLoadedAliases(result.data.model_aliases);
        setHasRestrictions(result.data.model_aliases.length > 0);
      } else {
        setAllowedAliases([]);
        setLoadedAliases([]);
        setHasRestrictions(false);
      }
      setLoaded(true);
    });
  }, [selectedTeamId]);

  const toggleModel = (alias: string) => {
    setAllowedAliases(prev =>
      prev.includes(alias) ? prev.filter(a => a !== alias) : [...prev, alias]
    );
  };

  // 순서 무관 비교 — 토글로 순서가 바뀌어도 dirty 가 아니다.
  const dirty =
    loaded &&
    [...allowedAliases].sort().join(',') !== [...loadedAliases].sort().join(',');

  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  const save = async (): Promise<boolean> => {
    if (!selectedTeamId || !loaded) return false;
    if (allowedAliases.length === 0) {
      toast({ type: 'error', message: t('minOneModel'), auto_dismiss_ms: 3000 });
      return false;
    }
    const result = await setTeamAllowedModelsAction(selectedTeamId, allowedAliases);
    if (result.success) {
      setLoadedAliases(allowedAliases);
      setHasRestrictions(true);
      if (!hideActions) {
        toast({ type: 'success', message: t('teamPermissionSaved'), auto_dismiss_ms: 3000 });
      }
      return true;
    }
    toast({ type: 'error', message: result.error, auto_dismiss_ms: 5000 });
    return false;
  };

  useImperativeHandle(ref, () => ({ save }));

  const handleSave = () => {
    startTransition(async () => {
      await save();
    });
  };

  const handleClear = () => {
    if (!selectedTeamId) return;
    startTransition(async () => {
      const result = await clearTeamAllowedModelsAction(selectedTeamId);
      if (result.success) {
        setAllowedAliases([]);
        setLoadedAliases([]);
        setHasRestrictions(false);
        toast({ type: 'success', message: t('restrictionCleared'), auto_dismiss_ms: 3000 });
      } else {
        toast({ type: 'error', message: result.error, auto_dismiss_ms: 5000 });
      }
    });
  };

  return (
    <div className="space-y-4 glass rounded-apple p-4">
      <div className="flex items-center gap-4">
        {!teamId && (
          <>
            <label className="text-sm font-medium">{t('selectTeam')}</label>
            <select
              value={pickedTeamId}
              onChange={e => setPickedTeamId(e.target.value)}
              className="rounded-md border border-input bg-background px-3 py-1.5 text-sm"
            >
              <option value="">{t('selectTeamPlaceholder')}</option>
              {visibleTeams.map(t => (
                <option key={t.id} value={t.id}>
                  {t.department_name ? `${t.name} (${t.department_name})` : t.name}
                </option>
              ))}
            </select>
          </>
        )}
        {!teamId && allTeams && allTeams.length > teams.length && (
          <label className="flex items-center gap-1.5 cursor-pointer text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={showInactive}
              onChange={e => setShowInactive(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-gray-300"
            />
            {t('includeInactiveTeams')}
          </label>
        )}
        {selectedTeamId && loaded && (
          hasRestrictions ? (
            <span className="badge badge-amber">{t('restrictionApplied')}</span>
          ) : (
            <span className="badge badge-teal">{t('noRestriction')}</span>
          )
        )}
      </div>

      {selectedTeamId && loaded && (
        <>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">{t('selectAllowedModels')}</span>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setAllowedAliases(activeModels.map(m => m.alias))}
                  className="text-xs text-primary hover:underline"
                >
                  {t('selectAll')}
                </button>
                <button
                  type="button"
                  onClick={() => setAllowedAliases([])}
                  className="text-xs text-muted-foreground hover:underline"
                >
                  {t('clearAll')}
                </button>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
              {activeModels.map(m => (
                <label key={m.alias} className="flex items-center gap-2 rounded-md border p-2 cursor-pointer hover:bg-muted/50">
                  <input
                    type="checkbox"
                    checked={allowedAliases.includes(m.alias)}
                    onChange={() => toggleModel(m.alias)}
                    className="h-4 w-4 rounded border-gray-300"
                  />
                  <span className="text-sm">{m.alias}</span>
                </label>
              ))}
            </div>
          </div>

          {/* 저장 버튼은 hideActions(통합 Apply)에서 숨기지만, 제한 해제 링크는
              별도 동작이므로 유지한다 — 행 자체는 둘 중 하나가 있을 때만 렌더. */}
          {(!hideActions || hasRestrictions) && (
            <div className="flex items-center gap-3 pt-2 border-t">
              {!hideActions && (
                <SpinnerButton
                  onClick={handleSave}
                  isLoading={isPending}
                  className="bg-primary text-primary-foreground hover:bg-primary/90 px-4 py-2 rounded-md text-sm font-medium"
                >
                  {t('savePermission')}
                </SpinnerButton>
              )}
              {hasRestrictions && (
                <button
                  type="button"
                  onClick={handleClear}
                  disabled={isPending}
                  className="text-sm text-destructive hover:underline disabled:opacity-50"
                >
                  {t('clearRestriction')}
                </button>
              )}
            </div>
          )}
        </>
      )}

      {selectedTeamId && !loaded && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground py-4">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          {t('loadingText')}
        </div>
      )}
    </div>
  );
});
