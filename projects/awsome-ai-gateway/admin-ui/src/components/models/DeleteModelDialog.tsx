'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.


import { useState, useTransition } from 'react';
import { useTranslations } from 'next-intl';
import { X } from 'lucide-react';
import type { ModelListItem } from '@/types/entities';
import { deleteModelAction } from '@/lib/actions/models';
import { FormError } from '@/components/common/FormError';
import { SpinnerButton } from '@/components/common/SpinnerButton';
import { useToast } from '@/components/common/ToastProvider';

interface DeleteModelDialogProps {
  isOpen: boolean;
  onClose: () => void;
  model: ModelListItem | null;
}

export function DeleteModelDialog({ isOpen, onClose, model }: DeleteModelDialogProps) {
  const t = useTranslations('models');
  const tCommon = useTranslations('common');
  const { toast } = useToast();
  const [isPending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [confirmText, setConfirmText] = useState('');

  if (!isOpen || !model) return null;

  // GitHub 식 type-to-confirm — alias 를 정확히 입력해야 삭제 버튼이 활성화된다.
  // alias 는 라우팅 키라 실수로 지우면 그 이름을 쓰는 클라이언트 요청이 전부 404.
  const canDelete = confirmText === model.alias;

  const handleClose = () => {
    setConfirmText('');
    setError(null);
    onClose();
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canDelete) return;
    setError(null);

    startTransition(async () => {
      const result = await deleteModelAction(model.alias);

      if (result.success) {
        toast({
          type: 'success',
          message: t('deleteSuccess', { alias: model.alias }),
          auto_dismiss_ms: 3000,
        });
        handleClose();
      } else {
        // 409(기본 모델 참조 중) 등 서버 메시지를 그대로 노출.
        setError(result.error);
      }
    });
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-background rounded-lg p-6 w-full max-w-md shadow-xl border border-border">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">{t('deleteTitle')}</h2>
          <button
            onClick={handleClose}
            className="rounded-sm opacity-70 hover:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring transition-opacity"
            aria-label={tCommon('close')}
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        <p className="text-sm text-muted-foreground mb-4">
          {t('deleteMessage', { alias: model.alias })}
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="delete-confirm-alias" className="block text-sm text-muted-foreground mb-1.5">
              {t('deleteTypeToConfirm', { alias: model.alias })}
            </label>
            <input
              id="delete-confirm-alias"
              type="text"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder={model.alias}
              autoComplete="off"
              autoFocus
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm font-mono focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            />
          </div>

          <FormError error={error} />

          <div className="flex items-center justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={handleClose}
              disabled={isPending}
              className="inline-flex items-center justify-center rounded-md border border-border bg-background px-4 py-2 text-sm font-medium hover:bg-accent transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
            >
              {tCommon('cancel')}
            </button>
            <SpinnerButton
              type="submit"
              isLoading={isPending}
              disabled={!canDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50"
            >
              {t('delete')}
            </SpinnerButton>
          </div>
        </form>
      </div>
    </div>
  );
}
