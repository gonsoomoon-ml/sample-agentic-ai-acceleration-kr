'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.


import { Fragment, useMemo, useState } from 'react';
import { useTranslations } from 'next-intl';
import { ChevronRight, ChevronDown } from 'lucide-react';
import type { BudgetSummaryItem, ModelListItem } from '@/types/entities';
import { AlertLevel, BudgetScope } from '@/types/enums';
import { Table, THead, TBody, Tr, Th, Td, TEmpty } from '@/components/common/Table';
import { SetBudgetDialog } from './SetBudgetDialog';
import { AutoDowngradeConfig } from './AutoDowngradeConfig';
import { AlertBadge, TypeBadge, UsageBar } from './budgetVisuals';

interface BudgetSummaryTableProps {
  items: BudgetSummaryItem[];
  isAdmin: boolean;
  models: ModelListItem[];
}

type DialogTarget = {
  id: string;
  name: string;
  type: (typeof BudgetScope)[keyof typeof BudgetScope];
  currentLimit: number;
  currentUsed?: number;
  parentLimit?: number;
};

const UNASSIGNED_KEY = '__unassigned__';

export function BudgetSummaryTable({ items, isAdmin, models }: BudgetSummaryTableProps) {
  const t = useTranslations('budgets');
  const [selectedItem, setSelectedItem] = useState<DialogTarget | null>(null);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [showInactive, setShowInactive] = useState(true);

  const alertLabels: Record<string, string> = {
    [AlertLevel.NORMAL]: t('alertLevels.NORMAL'),
    [AlertLevel.WARNING]: t('alertLevels.WARNING'),
    [AlertLevel.CRITICAL]: t('alertLevels.CRITICAL'),
  };
  const typeLabels: Record<string, string> = {
    [BudgetScope.TEAM]: t('scope.TEAM'),
    [BudgetScope.USER]: t('scope.USER'),
  };

  const hasInactive = items.some(i => i.is_active === false);
  const filteredItems = showInactive ? items : items.filter(i => i.is_active !== false);

  const { teamRows, usersByTeam, unassignedUsers } = useMemo(() => {
    const teams = filteredItems.filter((i) => i.target_type === BudgetScope.TEAM);
    const users = filteredItems.filter((i) => i.target_type === BudgetScope.USER);
    const grouped: Record<string, BudgetSummaryItem[]> = {};
    const orphans: BudgetSummaryItem[] = [];
    for (const u of users) {
      if (u.team_id) {
        (grouped[u.team_id] ??= []).push(u);
      } else {
        orphans.push(u);
      }
    }
    return { teamRows: teams, usersByTeam: grouped, unassignedUsers: orphans };
  }, [filteredItems]);

  const handleOpenDialog = (item: BudgetSummaryItem) => {
    setSelectedItem({
      id: item.target_id,
      name: item.target_name,
      type: item.target_type,
      currentLimit: item.limit ?? 0,
      currentUsed: item.used,
    });
    setIsDialogOpen(true);
  };

  const handleCloseDialog = () => {
    setIsDialogOpen(false);
    setSelectedItem(null);
  };

  const toggle = (key: string) => {
    setExpanded((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const colCount = isAdmin ? 8 : 7;
  const isEmpty = teamRows.length === 0 && unassignedUsers.length === 0;

  const renderUserRow = (user: BudgetSummaryItem) => (
    <Tr key={user.target_id} className="bg-muted/10">
      <Td emphasis>
        <div className="flex items-center gap-2 pl-10">
          <span className="text-muted-foreground" aria-hidden="true">
            └
          </span>
          {user.target_name}
        </div>
      </Td>
      <Td>
        <TypeBadge type={user.target_type} labels={typeLabels} />
      </Td>
      <Td numeric>
        {user.limit != null ? `$${user.limit.toFixed(2)}` : <span className="text-muted-foreground italic">{t('teamBudgetApplied')}</span>}
      </Td>
      <Td numeric>${user.used.toFixed(2)}</Td>
      <Td numeric>
        {user.remaining != null ? `$${user.remaining.toFixed(2)}` : <span className="text-muted-foreground italic">-</span>}
      </Td>
      <Td>
        <div className="flex items-center gap-2">
          {user.usage_pct != null ? (
            <>
              <UsageBar pct={user.usage_pct} level={user.alert_level} />
              <span className="w-12 text-right text-[11px] tabular-nums text-muted-foreground whitespace-nowrap">
                {user.usage_pct.toFixed(1)}%
              </span>
            </>
          ) : (
            <span className="text-xs text-muted-foreground italic">-</span>
          )}
        </div>
      </Td>
      <Td>
        <AlertBadge level={user.alert_level} labels={alertLabels} />
      </Td>
      {isAdmin && (
        <Td>
          <button
            onClick={() => handleOpenDialog(user)}
            className="inline-flex items-center justify-center rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium hover:bg-accent transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            {t('setBudget')}
          </button>
        </Td>
      )}
    </Tr>
  );

  return (
    <>
      {hasInactive && (
        <div className="flex items-center gap-2 mb-3">
          <label className="flex items-center gap-1.5 cursor-pointer text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={showInactive}
              onChange={e => setShowInactive(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-border"
            />
            {t('includeInactive')}
          </label>
        </div>
      )}
      <div className="w-full glass rounded-apple overflow-hidden">
        <Table>
          <THead>
            <Tr>
              <Th>{t('targetName')}</Th>
              <Th>{t('type')}</Th>
              <Th numeric>{t('maxBudget')}</Th>
              <Th numeric>{t('used')}</Th>
              <Th numeric>{t('remainingBudget')}</Th>
              <Th className="min-w-[120px]">{t('usageRate')}</Th>
              <Th>{t('status')}</Th>
              {isAdmin && <Th>{t('actions')}</Th>}
            </Tr>
          </THead>
          <TBody>
            {isEmpty ? (
              <TEmpty colSpan={colCount}>{t('noData')}</TEmpty>
            ) : (
              <>
                {teamRows.map((team) => {
                  const members = usersByTeam[team.target_id] ?? [];
                  const isOpen = expanded[team.target_id] ?? false;
                  const hasMembers = members.length > 0;
                  return (
                    <Fragment key={team.target_id}>
                      <Tr>
                        <Td emphasis>
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => toggle(team.target_id)}
                              aria-expanded={isOpen}
                              aria-label={
                                isOpen
                                  ? t('collapse', { name: team.target_name })
                                  : t('expand', { name: team.target_name })
                              }
                              className="flex h-5 w-5 items-center justify-center rounded hover:bg-muted text-muted-foreground"
                            >
                              {isOpen ? (
                                <ChevronDown size={14} />
                              ) : (
                                <ChevronRight size={14} />
                              )}
                            </button>
                            <span>{team.target_name}</span>
                            {hasMembers && (
                              <span className="text-xs text-muted-foreground">
                                ({members.length})
                              </span>
                            )}
                          </div>
                        </Td>
                        <Td>
                          <TypeBadge type={team.target_type} labels={typeLabels} />
                        </Td>
                        <Td numeric>
                          {team.limit != null ? `$${team.limit.toFixed(2)}` : <span className="text-muted-foreground italic">{t('notSet')}</span>}
                        </Td>
                        <Td numeric>${team.used.toFixed(2)}</Td>
                        <Td numeric>
                          {team.remaining != null ? `$${team.remaining.toFixed(2)}` : <span className="text-muted-foreground italic">-</span>}
                        </Td>
                        <Td>
                          <div className="flex items-center gap-2">
                            {team.usage_pct != null ? (
                              <>
                                <UsageBar pct={team.usage_pct} level={team.alert_level} />
                                <span className="w-12 text-right text-[11px] tabular-nums text-muted-foreground whitespace-nowrap">
                                  {team.usage_pct.toFixed(1)}%
                                </span>
                              </>
                            ) : (
                              <span className="text-xs text-muted-foreground italic">-</span>
                            )}
                          </div>
                        </Td>
                        <Td>
                          <AlertBadge level={team.alert_level} labels={alertLabels} />
                        </Td>
                        {isAdmin && (
                          <Td>
                            <button
                              onClick={() => handleOpenDialog(team)}
                              className="inline-flex items-center justify-center rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium hover:bg-accent transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                            >
                              {t('setBudget')}
                            </button>
                          </Td>
                        )}
                      </Tr>
                      {isOpen && members.map(renderUserRow)}
                      {isOpen && (
                        <Tr className="bg-muted/10">
                          <Td colSpan={colCount}>
                            <div className="pl-10 py-2">
                              <AutoDowngradeConfig
                                scopeType="TEAM"
                                scopeId={team.target_id}
                                scopeName={team.target_name}
                                models={models}
                              />
                            </div>
                          </Td>
                        </Tr>
                      )}
                    </Fragment>
                  );
                })}

                {unassignedUsers.length > 0 && (() => {
                  const isOpen = expanded[UNASSIGNED_KEY] ?? false;
                  return (
                    <Fragment key={UNASSIGNED_KEY}>
                      <Tr>
                        <Td emphasis colSpan={colCount}>
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => toggle(UNASSIGNED_KEY)}
                              aria-expanded={isOpen}
                              aria-label={isOpen ? t('collapseUnassigned') : t('expandUnassigned')}
                              className="flex h-5 w-5 items-center justify-center rounded hover:bg-muted text-muted-foreground"
                            >
                              {isOpen ? (
                                <ChevronDown size={14} />
                              ) : (
                                <ChevronRight size={14} />
                              )}
                            </button>
                            <span className="text-muted-foreground">{t('unassigned')}</span>
                            <span className="text-xs text-muted-foreground">
                              ({unassignedUsers.length})
                            </span>
                          </div>
                        </Td>
                      </Tr>
                      {isOpen && unassignedUsers.map(renderUserRow)}
                    </Fragment>
                  );
                })()}
              </>
            )}
          </TBody>
        </Table>
      </div>

      <SetBudgetDialog
        key={selectedItem?.id ?? 'none'}
        isOpen={isDialogOpen}
        onClose={handleCloseDialog}
        target={selectedItem}
      />
    </>
  );
}