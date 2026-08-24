'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.


import { useMemo, useState } from 'react';
import { useTranslations } from 'next-intl';
import type { OrgTreeNode, RateLimitTreeNode } from '@/types/entities';
import { RateLimitScope } from '@/types/enums';
import { OrgTree } from '@/components/users/OrgTree';
import { RateLimitTree } from './RateLimitTree';
import { RateLimitConfigPanel } from './RateLimitConfigPanel';

interface RateLimitTreeViewProps {
  root: OrgTreeNode | null;
  rateTree: RateLimitTreeNode[];
}

function collectAllIds(node: OrgTreeNode, set: Set<string>) {
  set.add(node.id);
  node.children.forEach((child) => collectAllIds(child, set));
}

function flattenRateTree(nodes: RateLimitTreeNode[]): Record<string, RateLimitTreeNode> {
  const map: Record<string, RateLimitTreeNode> = {};
  const walk = (n: RateLimitTreeNode) => {
    map[n.id] = n;
    n.children.forEach(walk);
  };
  nodes.forEach(walk);
  return map;
}

export function RateLimitTreeView({ root, rateTree }: RateLimitTreeViewProps) {
  const t = useTranslations('rateLimits');
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(null);
  const [selectedRateLimit, setSelectedRateLimit] = useState<RateLimitTreeNode | null>(null);
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(() => {
    const set = new Set<string>();
    if (root) collectAllIds(root, set);
    return set;
  });

  const rateLimitMap = useMemo(() => flattenRateTree(rateTree), [rateTree]);
  const globals = rateTree.filter((n) => n.scope === RateLimitScope.GLOBAL);

  const handleToggle = (id: string) => {
    setExpandedNodes((prev: Set<string>) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleOrgSelect = (node: OrgTreeNode) => {
    setSelectedOrgId(node.id);
    setSelectedRateLimit(rateLimitMap[node.id] ?? null);
  };

  const handleGlobalSelect = (node: RateLimitTreeNode) => {
    setSelectedOrgId(null);
    setSelectedRateLimit(node);
  };

  return (
    <div className="space-y-3">
      <div className="flex gap-0 border rounded-lg overflow-hidden min-h-[600px]">
        <div className="w-72 border-r overflow-y-auto py-1">
          {globals.length > 0 && (
            <div className="border-b pb-2 mb-2">
              <p className="px-3 py-1.5 text-xs font-semibold text-muted-foreground">{t('scopeLabel.GLOBAL')}</p>
              {/* OrgTree 와 expandedNodes Set 를 공유하지만, RateLimitTreeNode.id 와
                  OrgTreeNode.id 는 서로 다른 네임스페이스라 충돌하지 않는다. */}
              <RateLimitTree
                nodes={globals}
                selectedNodeId={selectedRateLimit?.scope === RateLimitScope.GLOBAL ? selectedRateLimit.id : null}
                expandedNodes={expandedNodes}
                onSelect={handleGlobalSelect}
                onToggle={handleToggle}
                depth={0}
              />
            </div>
          )}
          {root ? (
            <OrgTree
              node={root}
              selectedNodeId={selectedOrgId}
              expandedNodes={expandedNodes}
              onSelect={handleOrgSelect}
              onToggle={handleToggle}
            />
          ) : (
            <p className="p-4 text-sm text-muted-foreground">{t('noConfigData')}</p>
          )}
        </div>
        <div className="flex-1 p-6">
          <RateLimitConfigPanel node={selectedRateLimit} />
        </div>
      </div>
    </div>
  );
}
