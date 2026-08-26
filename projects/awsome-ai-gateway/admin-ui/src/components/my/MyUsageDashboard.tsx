'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.


import { useTranslations } from 'next-intl';
import type { MyUsageResponse } from '@/lib/actions/my';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/common/Table';

export function MyUsageDashboard({ data }: { data: MyUsageResponse }) {
  const t = useTranslations('my');
  const totalCost = data.daily_usage.reduce((sum, d) => sum + d.cost_usd, 0);
  const totalRequests = data.daily_usage.reduce((sum, d) => sum + d.requests, 0);
  const totalTokens = data.daily_usage.reduce((sum, d) => sum + d.tokens, 0);

  return (
    <div className="space-y-6">
      {/* Summary Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="glass glass-hover rounded-apple p-4">
          <p className="text-sm text-muted-foreground">{t('totalCost')}</p>
          <p className="text-2xl font-bold mt-1">${totalCost.toFixed(4)}</p>
        </div>
        <div className="glass glass-hover rounded-apple p-4">
          <p className="text-sm text-muted-foreground">{t('totalRequests')}</p>
          <p className="text-2xl font-bold mt-1">{totalRequests.toLocaleString()}</p>
        </div>
        <div className="glass glass-hover rounded-apple p-4">
          <p className="text-sm text-muted-foreground">{t('totalTokens')}</p>
          <p className="text-2xl font-bold mt-1">{totalTokens.toLocaleString()}</p>
        </div>
      </div>

      {/* Daily Usage Table */}
      <div className="glass rounded-apple overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <h3 className="text-sm font-semibold">{t('dailyUsage')}</h3>
        </div>
        {data.daily_usage.length === 0 ? (
          <p className="text-sm text-muted-foreground p-4">{t('noDailyRecords')}</p>
        ) : (
          <Table>
            <THead>
              <Tr>
                <Th>{t('colDate')}</Th>
                <Th numeric>{t('colCostUsd')}</Th>
                <Th numeric>{t('colRequests')}</Th>
                <Th numeric>{t('colTokens')}</Th>
              </Tr>
            </THead>
            <TBody>
              {data.daily_usage.map((row) => (
                <Tr key={row.date}>
                  <Td className="num">{row.date}</Td>
                  <Td numeric>${row.cost_usd.toFixed(4)}</Td>
                  <Td numeric>{row.requests.toLocaleString()}</Td>
                  <Td numeric>{row.tokens.toLocaleString()}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        )}
      </div>

      {/* By Model Table */}
      <div className="glass rounded-apple overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <h3 className="text-sm font-semibold tracking-tight">{t('modelUsage')}</h3>
        </div>
        {data.by_model.length === 0 ? (
          <p className="text-sm text-muted-foreground p-4">{t('noModelRecords')}</p>
        ) : (
          <Table>
            <THead>
              <Tr>
                <Th>{t('colModel')}</Th>
                <Th numeric>{t('colCostUsd')}</Th>
                <Th numeric>{t('colRequests')}</Th>
                <Th numeric>{t('colTokens')}</Th>
              </Tr>
            </THead>
            <TBody>
              {data.by_model.map((row) => (
                <Tr key={row.model_alias}>
                  <Td emphasis className="font-mono mono-id text-xs">{row.model_alias}</Td>
                  <Td numeric>${row.cost_usd.toFixed(4)}</Td>
                  <Td numeric>{row.requests.toLocaleString()}</Td>
                  <Td numeric>{row.tokens.toLocaleString()}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        )}
      </div>
    </div>
  );
}
