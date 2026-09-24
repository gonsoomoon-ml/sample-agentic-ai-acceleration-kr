'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.


import { useTranslations } from 'next-intl';
import type { MyBudgetResponse } from '@/lib/actions/my';

const KNOWN_POLICIES = ['HARD_BLOCK', 'SOFT_WARNING', 'THROTTLE'];

function usageColor(pct: number) {
  if (pct >= 90) return 'text-red-600';
  if (pct >= 70) return 'text-yellow-600';
  return 'text-green-600';
}

function barColor(pct: number) {
  if (pct >= 90) return 'bg-red-500';
  if (pct >= 70) return 'bg-yellow-500';
  return 'bg-green-500';
}

export function MyBudgetCard({ data }: { data: MyBudgetResponse }) {
  const t = useTranslations('my');
  const b = data.budget;
  const pct = Math.min(b.usage_pct, 100);

  const policy = KNOWN_POLICIES.includes(b.policy)
    ? t(`policyLabel.${b.policy}`)
    : b.policy;

  return (
    <div className="glass glass-hover rounded-apple p-6">
      <h2 className="text-base font-semibold mb-4">{t('monthlyBudget')}</h2>
      <div className="space-y-3">
        <div className="flex items-end justify-between">
          <span className="text-sm text-muted-foreground">{t('spent')}</span>
          <span className="text-2xl font-bold">${b.used_usd.toFixed(2)}</span>
        </div>

        <div className="w-full h-3 bg-muted rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${barColor(pct)}`}
            style={{ width: `${pct}%` }}
          />
        </div>

        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>{t('limit', { amount: b.limit_usd.toFixed(2) })}</span>
          <span className={usageColor(pct)}>{b.usage_pct.toFixed(1)}%</span>
        </div>

        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">{t('remaining')}</span>
          <span className="font-medium">${b.remaining_usd.toFixed(2)}</span>
        </div>
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">{t('overagePolicy')}</span>
          <span className="font-medium">{policy}</span>
        </div>
      </div>
    </div>
  );
}