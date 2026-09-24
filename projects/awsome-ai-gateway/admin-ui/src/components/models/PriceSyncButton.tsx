'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

import { useState, useTransition } from 'react';
import { useTranslations } from 'next-intl';
import { RefreshCw, Loader2, X } from 'lucide-react';
import {
  previewPriceSyncAction,
  applyPriceSyncAction,
  type PriceSyncPreview,
} from '@/lib/actions/models';
import { useToast } from '@/components/common/ToastProvider';
import { fmtPricePerM } from '@/lib/utils/pricing';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/common/Table';

/**
 * 외부 단가 소스(AWS Price List / LiteLLM Catalog) 동기화 버튼 + diff 미리보기/승인 다이얼로그.
 *
 * 흐름(자동적용 금지): 버튼 → preview(읽기) → diff 표 → 변경분 선택 → 적용(승인).
 * 적용은 기존 set_pricing 경로라 시계열·감사·캐시무효화가 보존된다.
 */
export function PriceSyncButton() {
  const t = useTranslations('models.priceSync');
  const tCommon = useTranslations('common');
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [source, setSource] = useState<'aws' | 'litellm'>('aws');
  const [preview, setPreview] = useState<PriceSyncPreview | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [applying, startApply] = useTransition();

  function openDialog() {
    setOpen(true);
    setPreview(null);
    setSelected(new Set());
  }

  async function runPreview() {
    setPreview(null);
    setSelected(new Set());
    setLoading(true);
    const res = await previewPriceSyncAction(source);
    setLoading(false);
    if (!res.success) {
      toast({ type: 'error', message: res.error || t('toast.previewFailed'), auto_dismiss_ms: 5000 });
      setOpen(false);
      return;
    }
    setPreview(res.data);
    // 기본 선택 = 변경분 전체(단가 변경 OR 스펙 갱신 — 스펙만 바뀌는 경우도 적용해야
    // context_window 가 채워진다).
    setSelected(new Set(res.data.diffs.filter((d) => d.matched && (d.changed || d.spec_changed)).map((d) => d.alias)));
  }

  function toggle(alias: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(alias)) next.delete(alias);
      else next.add(alias);
      return next;
    });
  }

  function apply() {
    const aliases = [...selected];
    if (!aliases.length) {
      toast({ type: 'error', message: t('toast.selectModel'), auto_dismiss_ms: 4000 });
      return;
    }
    startApply(async () => {
      const res = await applyPriceSyncAction(aliases, source);
      if (!res.success) {
        toast({ type: 'error', message: res.error || t('toast.applyFailed'), auto_dismiss_ms: 5000 });
        return;
      }
      const { applied, skipped, errors } = res.data;
      toast({ type: 'success', message: t('toast.applySuccess', { applied: applied.length, skipped: skipped.length }), auto_dismiss_ms: 4000 });
      if (errors.length) toast({ type: 'error', message: t('toast.errorDetail', { count: errors.length, message: errors[0] }), auto_dismiss_ms: 6000 });
      setOpen(false);
    });
  }

  const changedDiffs = preview?.diffs.filter((d) => d.matched && (d.changed || d.spec_changed)) ?? [];
  const unchanged = preview?.diffs.filter((d) => d.matched && !d.changed && !d.spec_changed).length ?? 0;
  const unmatched = preview?.diffs.filter((d) => !d.matched) ?? [];

  return (
    <>
      <button
        onClick={openDialog}
        className="inline-flex items-center justify-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm font-medium transition-colors hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        title={t('buttonTitle')}
      >
        <RefreshCw size={15} />
        {t('button')}
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="glass max-h-[85vh] w-full max-w-3xl overflow-hidden rounded-apple flex flex-col">
            <div className="flex items-center justify-between border-b border-border px-5 py-3">
              <h2 className="text-base font-semibold">{t('dialogTitle')}</h2>
              <button onClick={() => setOpen(false)} className="text-muted-foreground hover:text-foreground">
                <X size={18} />
              </button>
            </div>

            <div className="overflow-auto px-5 py-4">
              <div className="mb-3 flex items-end gap-2">
                <div className="flex-1">
                  <label className="mb-1 block text-xs text-muted-foreground">
                    {t('sourceLabel')}
                  </label>
                  <select
                    value={source}
                    onChange={(e) => setSource(e.target.value as 'aws' | 'litellm')}
                    disabled={loading || applying}
                    className="block w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
                  >
                    <option value="aws">{t('sourceAws')}</option>
                    <option value="litellm">{t('sourceThirdParty')}</option>
                  </select>
                </div>
                <button
                  type="button"
                  onClick={runPreview}
                  disabled={loading || applying}
                  className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                  {loading && <Loader2 size={14} className="animate-spin" />}
                  {t('previewButton')}
                </button>
              </div>

              {loading && (
                <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
                  <Loader2 size={16} className="animate-spin" /> {t('loading')}
                </div>
              )}

              {preview && (
                <>
                  <p className="mb-3 text-xs text-muted-foreground">
                    {t('sourceInfo', {
                      source: source === 'aws' ? 'AWS Price List API' : t('sourceThirdParty'),
                      region: preview.region,
                      matched: preview.matched_count,
                      changed: preview.changed_count,
                      unchanged,
                      unmatched: unmatched.length,
                    })}
                  </p>

                  {changedDiffs.length === 0 ? (
                    <p className="py-6 text-sm text-muted-foreground">
                      {t('noChanges')}
                    </p>
                  ) : (
                    <Table density="compact">
                      <THead>
                        <Tr>
                          <Th>{t('columns.apply')}</Th>
                          <Th>{t('columns.model')}</Th>
                          <Th>{t('columns.modelId')}</Th>
                          <Th numeric>{t('columns.input')}</Th>
                          <Th numeric>{t('columns.output')}</Th>
                          <Th numeric>{t('columns.cache5m')}</Th>
                          <Th numeric>{t('columns.cache1h')}</Th>
                          <Th numeric>{t('columns.cacheRead')}</Th>
                          <Th>{t('columns.note')}</Th>
                        </Tr>
                      </THead>
                      <TBody>
                        {changedDiffs.map((d) => (
                          <Tr key={d.alias}>
                            <Td>
                              <input
                                type="checkbox"
                                checked={selected.has(d.alias)}
                                onChange={() => toggle(d.alias)}
                              />
                            </Td>
                            <Td>{d.alias}</Td>
                            <Td className="text-xs text-muted-foreground">{d.provider_model_id}</Td>
                            <Td numeric>{fmtChange(d.current?.input_price_per_1k_tokens, d.proposed_input_per_1k)}</Td>
                            <Td numeric>{fmtChange(d.current?.output_price_per_1k_tokens, d.proposed_output_per_1k)}</Td>
                            <Td numeric>{fmtChange(d.current?.cache_creation_5m_price_per_1k_tokens, d.proposed_cache_5m_per_1k)}</Td>
                            <Td numeric>{fmtChange(d.current?.cache_creation_1h_price_per_1k_tokens, d.proposed_cache_1h_per_1k)}</Td>
                            <Td numeric>{fmtChange(d.current?.cache_read_price_per_1k_tokens, d.proposed_cache_read_per_1k)}</Td>
                            <Td>{d.note ? <span className="text-amber-600 dark:text-amber-400 text-[11px]">{d.note}</span> : ''}</Td>
                          </Tr>
                        ))}
                      </TBody>
                    </Table>
                  )}

                  {unmatched.length > 0 && (
                    <p className="mt-3 text-[11px] text-muted-foreground">
                      {t('unmatched', { aliases: unmatched.map((d) => d.alias).join(', ') })}
                    </p>
                  )}
                </>
              )}
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
              <button
                onClick={() => setOpen(false)}
                className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-accent/50"
              >
                {tCommon('cancel')}
              </button>
              <button
                onClick={apply}
                disabled={applying || !preview || selected.size === 0}
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                {applying && <Loader2 size={14} className="animate-spin" />}
                {t('applySelected', { count: selected.size })}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function fmtPrice(value: string | undefined | null): string {
  if (value == null) return '—';
  return fmtPricePerM(value);  // per-1K 저장값 → per-1M 표시
}

function fmtChange(current: string | undefined | null, proposed: string | null): string {
  const c = fmtPrice(current);
  const p = proposed != null ? fmtPricePerM(proposed) : '—';
  return `${c} → ${p}`;
}
