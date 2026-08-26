// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

import { adminAPI } from '@/lib/api-client';
import { getTranslations } from 'next-intl/server';
import Link from 'next/link';
import { Suspense } from 'react';
import { SkeletonTable } from '@/components/common/SkeletonTable';
import { KeysTable } from '@/components/keys/KeysTable';
import { KeyStatus } from '@/types/enums';
import type { VirtualKeyListItem } from '@/types/entities';

interface CursorPaginationMeta {
  cursor: string | null;
  limit: number;
  has_more: boolean;
}

interface KeyListResponse {
  items: VirtualKeyListItem[];
  pagination: CursorPaginationMeta;
}

const PAGE_LIMIT = 50;

interface KeysPageProps {
  searchParams?: { email?: string; status?: string };
}

export default async function KeysPage({ searchParams }: KeysPageProps) {
  const t = await getTranslations('keys');
  const email = searchParams?.email?.trim() ?? '';
  const rawStatus = searchParams?.status?.toUpperCase() as KeyStatus | undefined;
  const currentStatus: KeyStatus =
    rawStatus && Object.values(KeyStatus).includes(rawStatus)
      ? rawStatus
      : KeyStatus.ACTIVE;

  const listQuery: Record<string, string | number> = { limit: PAGE_LIMIT };
  if (email) listQuery.email = email;
  listQuery.status = currentStatus;

  const keysData = await adminAPI
    .get<KeyListResponse>('/admin/keys', listQuery)
    .catch(() => ({
      items: [] as VirtualKeyListItem[],
      pagination: { cursor: null, limit: PAGE_LIMIT, has_more: false },
    }));

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold">{t('pageTitle')}</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {t('pageDescription')}
        </p>
      </div>

      <form method="GET" className="flex items-center gap-2">
        <input
          type="search"
          name="email"
          defaultValue={email}
          placeholder={t('searchPlaceholder')}
          className="w-80 rounded-md border border-border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        />
        <button
          type="submit"
          className="inline-flex items-center rounded-md border border-border bg-background px-3 py-2 text-sm font-medium text-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
        >
          {t('searchButton')}
        </button>
        {email && (
          <Link
            href="/keys"
            className="text-sm text-muted-foreground hover:text-foreground underline-offset-4 hover:underline"
          >
            {t('resetSearch')}
          </Link>
        )}
      </form>

      <div className="flex flex-wrap items-center gap-2 mt-4">
        {Object.values(KeyStatus).map((status) => {
          const isActive = status === currentStatus;
          const params = new URLSearchParams();
          params.set('status', status);
          if (email) params.set('email', email);
          return (
            <Link
              key={status}
              href={`/keys?${params.toString()}`}
              className={`inline-flex items-center rounded-md border px-3 py-1.5 text-sm font-medium transition-colors ${
                isActive
                  ? 'bg-primary text-primary-foreground border-primary'
                  : 'bg-background text-foreground border-border hover:bg-accent'
              }`}
            >
              {t(`keyStatus.${status}` as const)}
            </Link>
          );
        })}
      </div>

      <Suspense fallback={<SkeletonTable rows={10} columns={6} />}>
        <div className="mt-4">
          <KeysTable keys={keysData.items} />
          {keysData.pagination.has_more && (
            <p className="mt-4 text-sm text-muted-foreground text-center">
              {t('moreKeysHint')}
            </p>
          )}
        </div>
      </Suspense>
    </div>
  );
}
