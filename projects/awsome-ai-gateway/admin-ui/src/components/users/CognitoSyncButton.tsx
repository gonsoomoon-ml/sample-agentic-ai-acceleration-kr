'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.


import { useState, useTransition } from 'react';
import { useTranslations } from 'next-intl';
import { RefreshCw } from 'lucide-react';
import { syncCognitoAction } from '@/lib/actions/users';
import { useToast } from '@/components/common/ToastProvider';

export function CognitoSyncButton() {
  const t = useTranslations('users');
  const { toast } = useToast();
  const [isPending, startTransition] = useTransition();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const handleSync = () => {
    startTransition(async () => {
      const result = await syncCognitoAction();
      setConfirmOpen(false);
      if (result.success) {
        const { groups_synced, users_created, users_updated, users_deactivated, errors } =
          result.data;
        const summary = [
          t('cognitoSync.groupsSynced', { count: groups_synced }),
          users_created > 0 ? t('cognitoSync.usersCreated', { count: users_created }) : null,
          users_updated > 0 ? t('cognitoSync.usersUpdated', { count: users_updated }) : null,
          users_deactivated > 0 ? t('cognitoSync.usersDeactivated', { count: users_deactivated }) : null,
        ]
          .filter(Boolean)
          .join(', ');

        toast({
          type: errors.length > 0 ? 'warning' : 'success',
          message: summary || t('cognitoSync.noChanges'),
          auto_dismiss_ms: 5000,
        });
      } else {
        toast({ type: 'error', message: result.error, auto_dismiss_ms: 4000 });
      }
    });
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setConfirmOpen(true)}
        disabled={isPending}
        className="inline-flex items-center gap-2 px-3 py-1.5 text-sm font-medium rounded-md border bg-background hover:bg-accent transition-colors disabled:opacity-50"
      >
        <RefreshCw size={14} className={isPending ? 'animate-spin' : ''} />
        {t('cognitoSync.button')}
      </button>

      {confirmOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-background border rounded-lg shadow-lg max-w-md w-full mx-4 p-6">
            <h3 className="text-base font-semibold mb-3">{t('cognitoSync.button')}</h3>
            <p className="text-sm text-muted-foreground mb-4">
              {t('cognitoSync.description')}
            </p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmOpen(false)}
                disabled={isPending}
                className="px-3 py-1.5 text-sm rounded-md border hover:bg-muted"
              >
                {t('common.cancel')}
              </button>
              <button
                type="button"
                onClick={handleSync}
                disabled={isPending}
                className="inline-flex items-center gap-2 px-3 py-1.5 text-sm font-medium rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                {isPending && <RefreshCw size={14} className="animate-spin" />}
                {isPending ? t('cognitoSync.running') : t('cognitoSync.run')}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}