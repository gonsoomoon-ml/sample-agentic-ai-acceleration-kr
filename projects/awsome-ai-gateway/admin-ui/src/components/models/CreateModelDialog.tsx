'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.


import { useState, useTransition, useEffect } from 'react';
import { useTranslations } from 'next-intl';
import { X } from 'lucide-react';
import type { ModelListItem } from '@/types/entities';
import { createModelAction, updateModelAction, listWireNamesAction, type WireNameItem } from '@/lib/actions/models';
import { perMtoPer1k, per1kToPerM } from '@/lib/utils/pricing';
import { FormError } from '@/components/common/FormError';
import { SpinnerButton } from '@/components/common/SpinnerButton';
import { useToast } from '@/components/common/ToastProvider';

interface CreateModelDialogProps {
  isOpen: boolean;
  onClose: () => void;
  editModel?: ModelListItem;
}

interface FormState {
  alias: string;
  provider: string;
  model_id: string;
  endpoint_url: string;
  input_price_per_1k: string;
  output_price_per_1k: string;
  cache_creation_5m_price_per_1k: string;
  cache_creation_1h_price_per_1k: string;
  cache_read_price_per_1k: string;
  description: string;
  display_name: string;
}

function getInitialState(editModel?: ModelListItem): FormState {
  if (editModel) {
    return {
      alias: editModel.alias,
      provider: editModel.provider,
      model_id: editModel.model_id,
      endpoint_url: editModel.endpoint_url ?? '',
      input_price_per_1k: per1kToPerM(editModel.input_price_per_1k),
      output_price_per_1k: per1kToPerM(editModel.output_price_per_1k),
      cache_creation_5m_price_per_1k: per1kToPerM(editModel.cache_creation_5m_price_per_1k),
      cache_creation_1h_price_per_1k: per1kToPerM(editModel.cache_creation_1h_price_per_1k),
      cache_read_price_per_1k: per1kToPerM(editModel.cache_read_price_per_1k),
      description: editModel.description ?? '',
      display_name: editModel.display_name ?? '',
    };
  }
  return {
    alias: '',
    provider: '',
    model_id: '',
    endpoint_url: '',
    input_price_per_1k: '',
    output_price_per_1k: '',
    cache_creation_5m_price_per_1k: '0',
    cache_creation_1h_price_per_1k: '0',
    cache_read_price_per_1k: '0',
    description: '',
    display_name: '',
  };
}

export function CreateModelDialog({ isOpen, onClose, editModel }: CreateModelDialogProps) {
  const t = useTranslations('models');
  const tCommon = useTranslations('common');
  const { toast } = useToast();
  const [isPending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [form, setForm] = useState<FormState>(() => getInitialState(editModel));

  const isEditMode = !!editModel;
  const [wireNames, setWireNames] = useState<WireNameItem[] | null>(null);
  const [showWireNames, setShowWireNames] = useState(false);

  useEffect(() => {
    if (editModel) {
      setForm(getInitialState(editModel));
    }
  }, [editModel]);

  // alias 생성 시 "클라이언트가 실제로 보내는 이름" 힌트 — usage_logs 에 관측된
  // 와이어 이름 목록. 클릭하면 alias 입력칸을 채운다.
  useEffect(() => {
    if (isOpen && !isEditMode && wireNames === null) {
      listWireNamesAction(30).then((r) => setWireNames(r.success ? r.data : []));
    }
  }, [isOpen, isEditMode, wireNames]);

  const handleChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>
  ) => {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
    if (fieldErrors[name]) {
      setFieldErrors((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setFieldErrors({});

    const payload = {
      alias: form.alias,
      provider: form.provider,
      model_id: form.model_id,
      endpoint_url: form.endpoint_url,
      input_price_per_1k: perMtoPer1k(parseFloat(form.input_price_per_1k)),
      output_price_per_1k: perMtoPer1k(parseFloat(form.output_price_per_1k)),
      cache_creation_5m_price_per_1k: perMtoPer1k(parseFloat(form.cache_creation_5m_price_per_1k || '0')),
      cache_creation_1h_price_per_1k: perMtoPer1k(parseFloat(form.cache_creation_1h_price_per_1k || '0')),
      cache_read_price_per_1k: perMtoPer1k(parseFloat(form.cache_read_price_per_1k || '0')),
      description: form.description || undefined,
      display_name: form.display_name || undefined,
    };

    startTransition(async () => {
      const result = isEditMode
        ? await updateModelAction(editModel.alias, payload)
        : await createModelAction(payload);

      if (result.success) {
        toast({
          type: 'success',
          message: isEditMode
            ? t('editSuccess', { alias: form.alias })
            : t('createSuccess', { alias: form.alias }),
          auto_dismiss_ms: 3000,
        });
        onClose();
      } else {
        // 필드 에러 중 이 폼에 대응 입력란이 없는 키는 아래 JSX 로 렌더될 수 없어 그냥 사라진다.
        // 실제로 그래서 max_tokens/context_window 스키마 드리프트가 원인 없는 "Validation failed"
        // 로만 보였다. 렌더 불가한 키는 상단 에러 메시지에 합쳐 항상 화면에 노출한다.
        const fe = (!result.success && result.fieldErrors) || {};
        const orphanKeys = Object.keys(fe).filter((k) => !(k in form));
        setError(
          orphanKeys.length
            ? `${result.error}: ${orphanKeys.map((k) => `${k} (${fe[k]})`).join(', ')}`
            : result.error
        );
        setFieldErrors(fe);
      }
    });
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-background rounded-lg p-6 w-full max-w-lg shadow-xl border border-border max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">
            {isEditMode ? t('editModel') : t('createModel')}
          </h2>
          <button
            onClick={onClose}
            className="rounded-sm opacity-70 hover:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring transition-opacity"
            aria-label={tCommon('close')}
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Alias */}
          <div className="space-y-1">
            <label htmlFor="alias" className="text-sm font-medium">
              Alias <span className="text-destructive">*</span>
            </label>
            <input
              id="alias"
              name="alias"
              type="text"
              value={form.alias}
              onChange={handleChange}
              required
              disabled={isEditMode}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-60 disabled:cursor-not-allowed"
              placeholder="e.g. claude-3-5-sonnet"
            />
            {isEditMode ? (
              <p className="text-xs text-muted-foreground">
                {t('aliasReadonly')}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">
                {t('aliasFieldHint')}
              </p>
            )}
            {fieldErrors.alias && <FormError error={fieldErrors.alias} />}

            {/* 실제 관측된 와이어 이름 — "어떤 이름으로 등록해야 하나"에 답하는 목록 */}
            {!isEditMode && (
              <div className="pt-1">
                <button
                  type="button"
                  onClick={() => setShowWireNames((v) => !v)}
                  aria-expanded={showWireNames}
                  className="text-xs text-primary hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring rounded-sm"
                >
                  {t('wireNamesToggle')}
                </button>
                {showWireNames && (
                  <div className="mt-1.5 rounded-md border border-border max-h-40 overflow-y-auto divide-y divide-border">
                    {wireNames === null ? (
                      <p className="px-3 py-2 text-xs text-muted-foreground">{tCommon('loading')}</p>
                    ) : wireNames.length === 0 ? (
                      <p className="px-3 py-2 text-xs text-muted-foreground">{t('wireNamesEmpty')}</p>
                    ) : (
                      wireNames.map((w) => (
                        <button
                          key={w.name}
                          type="button"
                          onClick={() => setForm((prev) => ({ ...prev, alias: w.name }))}
                          className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
                        >
                          <span className="font-mono mono-id text-xs truncate">{w.name}</span>
                          <span className="flex items-center gap-2 shrink-0">
                            {w.rejected_count > 0 && (
                              <span className="text-[10px] font-medium text-destructive tabular-nums">
                                {t('wireNameRejected', { count: w.rejected_count })}
                              </span>
                            )}
                            {w.request_count > 0 && (
                              <span className="text-[10px] text-muted-foreground tabular-nums">
                                {t('wireNameCount', { count: w.request_count })}
                              </span>
                            )}
                            <span
                              className={`text-[10px] px-1.5 py-0.5 rounded ${
                                w.registered
                                  ? 'bg-teal-500/15 text-teal-700 dark:text-teal-300'
                                  : 'bg-amber-500/15 text-amber-700 dark:text-amber-300'
                              }`}
                            >
                              {w.registered ? t('wireNameRegistered') : t('wireNameUnregistered')}
                            </span>
                          </span>
                        </button>
                      ))
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Provider — ⚠️ 편집 모드에서는 alias 와 마찬가지로 읽기 전용이다.
              PUT /admin/models/{alias} 의 ModelUpdateRequest 에는 provider/api_format 이
              없고(admin-api/src/app/schemas/models.py) update 서비스·리포지토리에도 변경
              경로가 없어 admin API 로는 바꿀 수 없는 불변 필드다. 예전엔 여기서 드롭다운을
              바꿀 수 있었고 updateModelAction 은 provider 를 아예 보내지도 않으므로
              (lib/actions/models.ts:95~) 성공 토스트만 뜨고 값은 반영되지 않았다 —
              화면과 DB 가 갈라지는 조용한 실패. 지금은 스키마가 422 로 거부하기도 하지만,
              애초에 저장 못 하는 컨트롤을 열어 두지 않는다. */}
          <div className="space-y-1">
            <label htmlFor="provider" className="text-sm font-medium">
              Provider <span className="text-destructive">*</span>
            </label>
            <select
              id="provider"
              name="provider"
              value={form.provider}
              onChange={handleChange}
              required
              disabled={isEditMode}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-60 disabled:cursor-not-allowed"
            >
              <option value="">{t('selectProvider')}</option>
              <option value="BEDROCK">BEDROCK</option>
              <option value="OPENMODEL">OPENMODEL</option>
              {/* Mantle 계열은 endpoint_url·api_format 이 필요해 보통 마이그레이션으로 시드되지만,
                  기존 cowork-opus / codex-gpt 모델 편집(단가 등) 시 provider 드롭다운이 값과
                  매칭되도록 옵션을 노출한다. */}
              <option value="BEDROCK_MANTLE">BEDROCK_MANTLE (Cowork · Opus)</option>
              <option value="BEDROCK_MANTLE_OPENAI">BEDROCK_MANTLE_OPENAI (Codex · GPT)</option>
              {/* 표준 bedrock-runtime plane (SigV4 + CRIS). Mantle 과 달리 endpoint_url 이
                  **필수**다 — 어댑터가 endpoint 호스트에서 서명 리전을 뽑아내므로
                  (bedrock-runtime.{region}.amazonaws.com) 비워 두면 서명할 수 없다.
                  provider_model_id 는 us./global. 접두사가 붙은 추론 프로파일 ID 여야 한다. */}
              <option value="BEDROCK_RUNTIME_OPENAI">BEDROCK_RUNTIME_OPENAI (GPT-5.6 · CRIS)</option>
            </select>
            {isEditMode && (
              <p className="text-xs text-muted-foreground">
                {t('providerReadonly')}
              </p>
            )}
            {fieldErrors.provider && <FormError error={fieldErrors.provider} />}
          </div>

          {/* Model ID */}
          <div className="space-y-1">
            <label htmlFor="model_id" className="text-sm font-medium">
              Model ID <span className="text-destructive">*</span>
            </label>
            <input
              id="model_id"
              name="model_id"
              type="text"
              value={form.model_id}
              onChange={handleChange}
              required
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              placeholder="e.g. anthropic.claude-3-5-sonnet-20241022-v2:0"
            />
            {fieldErrors.model_id && <FormError error={fieldErrors.model_id} />}
          </div>

          {/* Endpoint URL — OPENMODEL(vLLM) 등 커스텀 엔드포인트 모델에서만 의미있음 */}
          <div className="space-y-1">
            <label htmlFor="endpoint_url" className="text-sm font-medium">
              {t('endpointUrl')} <span className="text-muted-foreground text-xs">{t('endpointUrlOptional')}</span>
            </label>
            <input
              id="endpoint_url"
              name="endpoint_url"
              type="text"
              value={form.endpoint_url}
              onChange={handleChange}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              placeholder={t('endpointUrlPlaceholder')}
            />
            {fieldErrors.endpoint_url && <FormError error={fieldErrors.endpoint_url} />}
          </div>

          {/* Price fields — 수직 일렬 배치 */}
          <div className="space-y-3">
            <span className="text-sm font-medium">{t('priceLabel')}</span>

            <div className="space-y-1">
              <label htmlFor="input_price_per_1k" className="text-xs text-muted-foreground">{t('priceInput')}</label>
              <input
                id="input_price_per_1k"
                name="input_price_per_1k"
                type="number"
                min={0}
                step={0.001}
                value={form.input_price_per_1k}
                onChange={handleChange}
                required
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                placeholder="0.00"
              />
              {fieldErrors.input_price_per_1k && <FormError error={fieldErrors.input_price_per_1k} />}
            </div>

            <div className="space-y-1">
              <label htmlFor="output_price_per_1k" className="text-xs text-muted-foreground">{t('priceOutput')}</label>
              <input
                id="output_price_per_1k"
                name="output_price_per_1k"
                type="number"
                min={0}
                step={0.001}
                value={form.output_price_per_1k}
                onChange={handleChange}
                required
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                placeholder="0.00"
              />
              {fieldErrors.output_price_per_1k && <FormError error={fieldErrors.output_price_per_1k} />}
            </div>

            <div className="space-y-1">
              <label htmlFor="cache_creation_5m_price_per_1k" className="text-xs text-muted-foreground">{t('priceCacheCreate5m')}</label>
              <input
                id="cache_creation_5m_price_per_1k"
                name="cache_creation_5m_price_per_1k"
                type="number"
                min={0}
                step={0.001}
                value={form.cache_creation_5m_price_per_1k}
                onChange={handleChange}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                placeholder="0.00"
              />
              {fieldErrors.cache_creation_5m_price_per_1k && <FormError error={fieldErrors.cache_creation_5m_price_per_1k} />}
            </div>

            <div className="space-y-1">
              <label htmlFor="cache_creation_1h_price_per_1k" className="text-xs text-muted-foreground">{t('priceCacheCreate1h')}</label>
              <input
                id="cache_creation_1h_price_per_1k"
                name="cache_creation_1h_price_per_1k"
                type="number"
                min={0}
                step={0.001}
                value={form.cache_creation_1h_price_per_1k}
                onChange={handleChange}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                placeholder="0.00"
              />
              {fieldErrors.cache_creation_1h_price_per_1k && <FormError error={fieldErrors.cache_creation_1h_price_per_1k} />}
            </div>

            <div className="space-y-1">
              <label htmlFor="cache_read_price_per_1k" className="text-xs text-muted-foreground">{t('priceCacheRead')}</label>
              <input
                id="cache_read_price_per_1k"
                name="cache_read_price_per_1k"
                type="number"
                min={0}
                step={0.001}
                value={form.cache_read_price_per_1k}
                onChange={handleChange}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                placeholder="0.00"
              />
              {fieldErrors.cache_read_price_per_1k && <FormError error={fieldErrors.cache_read_price_per_1k} />}
            </div>
          </div>

          {/* Description */}
          <div className="space-y-1">
            <label htmlFor="description" className="text-sm font-medium">
              {t('descriptionLabel')} <span className="text-muted-foreground text-xs">({t('descriptionOptional')})</span>
            </label>
            <textarea
              id="description"
              name="description"
              value={form.description}
              onChange={handleChange}
              rows={3}
              maxLength={512}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-none"
              placeholder={t('descriptionPlaceholder')}
            />
            {fieldErrors.description && <FormError error={fieldErrors.description} />}
          </div>

          {/* Display Name */}
          <div className="space-y-1">
            <label htmlFor="display_name" className="text-sm font-medium">
              {t('displayNameLabel')}
            </label>
            <input
              id="display_name"
              name="display_name"
              type="text"
              maxLength={128}
              value={form.display_name}
              onChange={handleChange}
              placeholder={t('displayNamePlaceholder')}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            />
            {fieldErrors.display_name && <FormError error={fieldErrors.display_name} />}
          </div>

          <FormError error={error} />

          <div className="flex items-center justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={isPending}
              className="inline-flex items-center justify-center rounded-md border border-border bg-background px-4 py-2 text-sm font-medium hover:bg-accent transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
            >
              {tCommon('cancel')}
            </button>
            <SpinnerButton type="submit" isLoading={isPending}>
              {isEditMode ? t('edit') : t('register')}
            </SpinnerButton>
          </div>
        </form>
      </div>
    </div>
  );
}