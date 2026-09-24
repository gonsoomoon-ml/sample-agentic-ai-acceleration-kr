'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.


import { useState, useTransition, useEffect } from 'react';
import { useTranslations } from 'next-intl';
import { TrendingDown, ArrowRight, Plus, X } from 'lucide-react';
import { useToast } from '@/components/common/ToastProvider';
import { SpinnerButton } from '@/components/common/SpinnerButton';
import { ConfirmDialog } from '@/components/common/ConfirmDialog';
import { DowngradeDiagram } from '@/components/common/DowngradeDiagram';
import {
  getDowngradeConfigAction,
  setDowngradeConfigAction,
  deleteDowngradeConfigAction,
} from '@/lib/actions/budgets';
import type { ModelListItem } from '@/types/entities';

interface DowngradeRuleForm {
  from_model_alias: string;
  to_model_alias: string;
  threshold_pct: string;
}

interface AutoDowngradeConfigProps {
  scopeType: 'TEAM' | 'USER';
  scopeId: string;
  scopeName: string;
  models: ModelListItem[];
}

export function AutoDowngradeConfig({ scopeType, scopeId, scopeName, models }: AutoDowngradeConfigProps) {
  const t = useTranslations('budgets');
  const tc = useTranslations('common');
  const { toast } = useToast();
  const [isPending, startTransition] = useTransition();
  const [enabled, setEnabled] = useState(false);
  const [rules, setRules] = useState<DowngradeRuleForm[]>([]);
  // 마지막으로 저장된 스냅샷 — 이 값과 같은 규칙 행은 "저장됨" 초록 테두리로 표시.
  // 편집하면 스냅샷과 어긋나 테두리가 풀리고, 새 규칙은 처음부터 테두리가 없다.
  const [savedRules, setSavedRules] = useState<DowngradeRuleForm[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);

  const activeModels = models.filter(m => m.is_active);

  const priceOf = (alias: string) =>
    activeModels.find(m => m.alias === alias)?.output_price_per_1k;

  // 다운그레이드는 output 단가가 낮은 모델로만 — from 보다 비싼 모델은 후보에서 제외.
  const cheaperModels = (fromAlias: string): ModelListItem[] => {
    const p = priceOf(fromAlias);
    if (p == null) return activeModels;
    return activeModels.filter(m => m.output_price_per_1k < p);
  };

  // 저장값은 per-1K — 표기는 1M 기준 output 단가가 읽기 쉽다 ($0.015/1K → $15/1M).
  const formatOutPrice = (m: ModelListItem) => {
    const per1m = m.output_price_per_1k * 1000;
    return `$${per1m.toFixed(2).replace(/\.?0+$/, '')}/1M output`;
  };

  useEffect(() => {
    if (!scopeId) return;
    startTransition(async () => {
      const result = await getDowngradeConfigAction(scopeType, scopeId);
      if (result.success) {
        setEnabled(result.data.enabled);
        const loaded = result.data.rules.map(r => ({
          from_model_alias: r.from_model_alias,
          to_model_alias: r.to_model_alias,
          threshold_pct: String(r.threshold_pct),
        }));
        setRules(loaded);
        setSavedRules(loaded);
      }
      setLoaded(true);
    });
  }, [scopeType, scopeId]);

  const addRule = () => {
    // 빈 규칙 — from/to 는 placeholder 를 보여주고 사용자가 고른다.
    setRules(prev => [
      ...prev,
      { from_model_alias: '', to_model_alias: '', threshold_pct: '80' },
    ]);
  };

  const removeRule = (index: number) => {
    setRules(prev => prev.filter((_, i) => i !== index));
  };

  const updateRule = (index: number, field: keyof DowngradeRuleForm, value: string | number) => {
    setRules(prev =>
      prev.map((r, i) => {
        if (i !== index) return r;
        const next = { ...r, [field]: value };
        // from 변경 시 기존 to 가 더 이상 저렴하지 않으면 첫 번째 유효 후보로 교체.
        if (field === 'from_model_alias' && !cheaperModels(next.from_model_alias).some(m => m.alias === next.to_model_alias)) {
          next.to_model_alias = cheaperModels(next.from_model_alias)[0]?.alias ?? '';
        }
        return next;
      }),
    );
  };

  const handleSave = () => {
    if (rules.length === 0) {
      toast({ type: 'error', message: t('minOneRule'), auto_dismiss_ms: 3000 });
      return;
    }
    for (const rule of rules) {
      if (!rule.from_model_alias) {
        toast({ type: 'error', message: t('selectFromModel'), auto_dismiss_ms: 3000 });
        return;
      }
      if (!rule.to_model_alias) {
        toast({ type: 'error', message: t('selectToModel'), auto_dismiss_ms: 3000 });
        return;
      }
      if (rule.from_model_alias === rule.to_model_alias) {
        toast({ type: 'error', message: t('sameSourceTarget', { alias: rule.from_model_alias }), auto_dismiss_ms: 3000 });
        return;
      }
      if (!cheaperModels(rule.from_model_alias).some(m => m.alias === rule.to_model_alias)) {
        toast({ type: 'error', message: t('targetNotCheaper', { from: rule.from_model_alias, to: rule.to_model_alias }), auto_dismiss_ms: 4000 });
        return;
      }
    }
    startTransition(async () => {
      const result = await setDowngradeConfigAction(scopeType, scopeId, {
        enabled,
        rules: rules.map(r => ({ ...r, threshold_pct: parseInt(r.threshold_pct) || 0 })),
      });
      if (result.success) {
        setSavedRules(rules.map(r => ({ ...r })));
        toast({ type: 'success', message: t('downgradeSaved'), auto_dismiss_ms: 3000 });
      } else {
        let msg = result.error;
        if (msg?.includes('Budget must be configured') || msg?.includes('must be greater than 0 for downgrade')) {
          msg = t('noBudgetForTeam');
        }
        toast({ type: 'error', message: msg, auto_dismiss_ms: 5000 });
      }
    });
  };

  const handleDisable = () => {
    startTransition(async () => {
      const result = await deleteDowngradeConfigAction(scopeType, scopeId);
      if (result.success) {
        setEnabled(false);
        setRules([]);
        setSavedRules([]);
        toast({ type: 'success', message: t('downgradeCleared'), auto_dismiss_ms: 3000 });
      } else {
        toast({ type: 'error', message: result.error, auto_dismiss_ms: 5000 });
      }
    });
  };

  if (!loaded) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground py-4">
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        {t('configLoading')}
      </div>
    );
  }

  return (
    <div className="space-y-4 rounded-apple border border-border/60 bg-muted/20 p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <TrendingDown size={17} />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold truncate">{t('autoDowngrade')}</h3>
              <span
                className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                  enabled
                    ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                    : 'bg-muted text-muted-foreground'
                }`}
              >
                {enabled ? tc('enabled') : tc('disabled')}
              </span>
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground truncate">
              {t('autoDowngradeDesc', { scope: scopeName })}
            </p>
          </div>
        </div>
        <label className="flex shrink-0 items-center gap-2 cursor-pointer pt-1">
          <div className="relative inline-flex items-center">
            <input
              type="checkbox"
              checked={enabled}
              onChange={e => setEnabled(e.target.checked)}
              className="sr-only peer"
            />
            <div className="w-10 h-[22px] bg-muted-foreground/30 peer-checked:bg-primary rounded-full transition-colors after:content-[''] after:absolute after:top-[3px] after:left-[3px] after:w-4 after:h-4 after:bg-background after:rounded-full after:shadow-sm after:transition-transform peer-checked:after:translate-x-[18px]" />
          </div>
        </label>
      </div>

      {enabled && (
        <div className="space-y-3">
          {rules.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border px-4 py-5 text-center text-xs text-muted-foreground">
              {t('noRules')}
            </div>
          ) : (
            <div className="space-y-2">
              {rules.map((rule, index) => (
                <div
                  key={index}
                  className={`group flex items-center gap-2 rounded-xl border px-3 py-2 ${
                    savedRules.some(
                      s =>
                        s.from_model_alias === rule.from_model_alias &&
                        s.to_model_alias === rule.to_model_alias &&
                        s.threshold_pct === rule.threshold_pct,
                    )
                      ? 'border-emerald-500/60 bg-emerald-500/5'
                      : 'border-border/60 bg-background/60'
                  }`}
                >
                  <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-muted text-[10px] font-semibold tabular-nums text-muted-foreground">
                    {index + 1}
                  </span>
                  <select
                    value={rule.from_model_alias}
                    onChange={e => updateRule(index, 'from_model_alias', e.target.value)}
                    className="min-w-0 flex-1 rounded-lg border border-transparent bg-transparent px-2 py-1.5 font-mono text-xs hover:bg-muted/50 focus:border-input focus:bg-background focus:outline-none focus:ring-1 focus:ring-ring"
                  >
                    {!rule.from_model_alias && (
                      <option value="" disabled>
                        {t('selectFromModel')}
                      </option>
                    )}
                    {activeModels.map(m => (
                      <option key={m.alias} value={m.alias}>
                        {m.alias} ({formatOutPrice(m)})
                      </option>
                    ))}
                  </select>
                  <ArrowRight size={14} className="shrink-0 text-muted-foreground" />
                  <select
                    value={rule.to_model_alias}
                    onChange={e => updateRule(index, 'to_model_alias', e.target.value)}
                    className="min-w-0 flex-1 rounded-lg border border-transparent bg-transparent px-2 py-1.5 font-mono text-xs hover:bg-muted/50 focus:border-input focus:bg-background focus:outline-none focus:ring-1 focus:ring-ring"
                  >
                    {!rule.to_model_alias && (
                      <option value="" disabled>
                        {rule.from_model_alias && cheaperModels(rule.from_model_alias).length === 0
                          ? t('noCheaperModel', { alias: rule.from_model_alias })
                          : t('selectToModel')}
                      </option>
                    )}
                    {/* 기존 저장분이 필터에 걸리는 경우 현재 값을 유지해 보여준다
                        (저장 시 검증에서 걸러짐) */}
                    {rule.to_model_alias &&
                      !cheaperModels(rule.from_model_alias).some(m => m.alias === rule.to_model_alias) && (
                        <option value={rule.to_model_alias}>
                          {rule.to_model_alias} — {tc('inactive')}
                        </option>
                      )}
                    {cheaperModels(rule.from_model_alias).map(m => (
                      <option key={m.alias} value={m.alias}>
                        {m.alias} ({formatOutPrice(m)})
                      </option>
                    ))}
                  </select>
                  <div className="flex shrink-0 items-center gap-1 rounded-lg border border-input bg-background px-2 py-1 focus-within:ring-1 focus-within:ring-ring">
                    <input
                      type="number"
                      min={1}
                      max={100}
                      value={rule.threshold_pct}
                      onChange={e => updateRule(index, 'threshold_pct', e.target.value)}
                      className="w-10 bg-transparent text-center text-xs tabular-nums focus:outline-none"
                    />
                    <span className="text-[10px] text-muted-foreground">%</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeRule(index)}
                    className="shrink-0 rounded-md p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
                    title={tc('delete')}
                    aria-label={tc('delete')}
                  >
                    <X size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}

          <button
            type="button"
            onClick={addRule}
            className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-dashed border-border px-3 py-2 text-xs font-medium text-muted-foreground transition-colors hover:border-primary/50 hover:bg-primary/5 hover:text-primary"
          >
            <Plus size={13} />
            {t('addRule')}
          </button>

          {rules.length > 0 && (
            <DowngradeDiagram
              rules={rules}
              models={activeModels}
              formatOutPrice={formatOutPrice}
            />
          )}
        </div>
      )}

      <div className="flex items-center gap-3 border-t border-border/60 pt-3">
        <SpinnerButton
          onClick={handleSave}
          isLoading={isPending}
          disabled={!enabled && rules.length === 0}
          className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {t('saveConfig')}
        </SpinnerButton>
        {enabled && (
          <button
            type="button"
            onClick={() => setClearOpen(true)}
            disabled={isPending}
            className="text-sm text-destructive hover:underline disabled:opacity-50"
          >
            {t('clearConfig')}
          </button>
        )}
      </div>

      <ConfirmDialog
        isOpen={clearOpen}
        onClose={() => setClearOpen(false)}
        onConfirm={handleDisable}
        title={t('clearConfigTitle')}
        message={t('clearConfigConfirm', { scope: scopeName })}
        confirmLabel={t('clearConfig')}
        isDestructive
      />
    </div>
  );
}
