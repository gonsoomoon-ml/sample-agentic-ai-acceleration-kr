'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.


import { useEffect, useRef, useState } from 'react';
import { useTranslations } from 'next-intl';
import type { OrgTreeNode } from '@/types/entities';
import { OrgTree } from './OrgTree';
import { OrgDetailPanel } from './OrgDetailPanel';

interface OrgTreeViewProps {
  root: OrgTreeNode | null;
}

const EXPANDED_NODES_STORAGE_KEY = 'users:orgtree:expandedNodes';

/** 초기 펼침: 조직(ORGANIZATION)과 그 직계 부서(DEPARTMENT)만 펼친다.
 *  팀(TEAM)과 사용자(USER)는 사용자가 클릭해서 펼치도록 한다. */
function getDefaultExpandedNodes(root: OrgTreeNode): Set<string> {
  const expanded = new Set<string>();
  expanded.add(root.id);
  for (const child of root.children ?? []) {
    if (child.type === 'DEPARTMENT') {
      expanded.add(child.id);
    }
  }
  return expanded;
}

export function OrgTreeView({ root }: OrgTreeViewProps) {
  const t = useTranslations('users');
  const [selectedNode, setSelectedNode] = useState<OrgTreeNode | null>(null);
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set());
  const hasMountedRef = useRef(false);

  // sessionStorage에서 펼침 상태 복원. 저장된 값이 없으면 기본값(조직+직계부서) 사용.
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(EXPANDED_NODES_STORAGE_KEY);
      if (raw) {
        const ids = JSON.parse(raw) as unknown;
        if (Array.isArray(ids) && ids.every((x) => typeof x === 'string')) {
          setExpandedNodes(new Set(ids as string[]));
          return;
        }
      }
      if (root) {
        setExpandedNodes(getDefaultExpandedNodes(root));
      }
    } catch {
      if (root) {
        setExpandedNodes(getDefaultExpandedNodes(root));
      }
    }
  }, [root]);

  // 펼침 상태 변경 시 persist (초기 빈 Set으로 덮어쓰지 않도록 첫 호출 skip)
  useEffect(() => {
    if (!hasMountedRef.current) {
      hasMountedRef.current = true;
      return;
    }
    try {
      sessionStorage.setItem(
        EXPANDED_NODES_STORAGE_KEY,
        JSON.stringify([...expandedNodes])
      );
    } catch {
      // quota/비활성 storage 무시
    }
  }, [expandedNodes]);

  // root 가 서버에서 갱신되면(router.refresh) selectedNode 도 새 트리의
  // 동일 id 노드로 동기화. 안 그러면 팀 리더 변경 후에도 우측 패널이 stale 상태로 남는다.
  useEffect(() => {
    if (!root || !selectedNode) return;

    const findById = (n: OrgTreeNode, id: string): OrgTreeNode | null => {
      if (n.id === id) return n;
      for (const child of n.children ?? []) {
        const found = findById(child, id);
        if (found) return found;
      }
      return null;
    };

    const next = findById(root, selectedNode.id);
    if (next && next !== selectedNode) {
      setSelectedNode(next);
    }
  }, [root, selectedNode?.id]);

  const handleToggle = (id: string) => {
    setExpandedNodes((prev) => {
      if (prev.has(id)) {
        return new Set([...prev].filter((x) => x !== id));
      }
      return new Set([...prev, id]);
    });
  };

  return (
    <div className="flex gap-0 border rounded-lg overflow-hidden min-h-[600px]">
      <div className="w-72 border-r overflow-y-auto">
        {root ? (
          <OrgTree
            node={root}
            selectedNodeId={selectedNode?.id ?? null}
            expandedNodes={expandedNodes}
            onSelect={setSelectedNode}
            onToggle={handleToggle}
          />
        ) : (
          <p className="p-4 text-muted-foreground text-sm">{t('noOrgData')}</p>
        )}
      </div>
      <div className="flex-1 p-6">
        <OrgDetailPanel node={selectedNode} />
      </div>
    </div>
  );
}