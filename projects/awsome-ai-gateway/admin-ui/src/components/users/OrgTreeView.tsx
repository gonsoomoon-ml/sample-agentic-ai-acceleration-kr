'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.


import { useEffect, useRef, useState } from 'react';
import { useTranslations } from 'next-intl';
import type { OrgTreeNode, UserSearchItem } from '@/types/entities';
import { OrgTree } from './OrgTree';
import { OrgDetailPanel } from './OrgDetailPanel';
import { OrgSearchBox } from './OrgSearchBox';
import { findTeamExpandPath, type OrgMatch } from '@/lib/utils/orgSearch';

interface OrgTreeViewProps {
  root: OrgTreeNode | null;
}

const EXPANDED_NODES_STORAGE_KEY = 'users:orgtree:expandedNodes';

export function OrgTreeView({ root }: OrgTreeViewProps) {
  const t = useTranslations('users');
  const [selectedNode, setSelectedNode] = useState<OrgTreeNode | null>(null);
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set());
  const hasMountedRef = useRef(false);

  // sessionStorage에서 펼침 상태 복원 (mount 1회)
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(EXPANDED_NODES_STORAGE_KEY);
      if (raw) {
        const ids = JSON.parse(raw) as unknown;
        if (Array.isArray(ids) && ids.every((x) => typeof x === 'string')) {
          setExpandedNodes(new Set(ids as string[]));
        }
      }
    } catch {
      // 손상/비활성 storage 무시
    }
  }, []);

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

  // ── 검색 결과 선택 ──────────────────────────────────────────────────────────

  /** 부서/팀: 조상 경로를 펼쳐 트리에 드러낸 뒤 선택한다. */
  const handleSelectOrgNode = (match: OrgMatch) => {
    const { node, ancestorIds } = match;
    // 팀이면 자신도 펼친다 — 멤버를 바로 보여주는 것이 클릭했을 때와 같은 결과다.
    const toExpand = node.type === 'TEAM' ? [...ancestorIds, node.id] : ancestorIds;
    setExpandedNodes((prev) => new Set([...prev, ...toExpand]));
    setSelectedNode(node);
  };

  /**
   * 사용자: 소속 팀 경로를 펼쳐 트리에서 위치를 드러내고, 상세 패널은 검색 결과로
   * 즉시 채운다.
   *
   * ⚠️ 검색 결과 항목으로 USER 노드를 **합성**한다. 트리에서 같은 사용자를 찾아 쓰지
   *    않는 이유는 두 가지다: (1) 팀이 트리에 없을 수 있고(멤버 0명 팀은 서버가
   *    숨긴다), (2) 팀 미배정 사용자는 트리에 자리가 없다. 두 경우에도 상세는 보여야
   *    한다. 합성 노드의 id 는 실제 노드와 같으므로 트리 하이라이트도 맞는다.
   */
  const handleSelectUser = (user: UserSearchItem) => {
    setSelectedNode({
      id: user.id,
      name: user.display_name,
      type: 'USER',
      children: [],
      meta: {
        member_count: null,
        team_count: null,
        leader_name: null,
        leader_user_id: null,
        email: user.email,
        role: user.role,
        team_name: user.team_name,
      },
    });

    if (!user.team_id) return; // 팀 미배정 — 트리에 드러낼 자리가 없다
    const path = findTeamExpandPath(root, user.team_id);
    if (!path) return; // 팀이 트리에 없다(멤버 0명 등) — 상세 패널만
    setExpandedNodes((prev) => new Set([...prev, ...path]));
  };

  const handleToggle = (id: string) => {
    setExpandedNodes((prev) => {
      if (prev.has(id)) {
        return new Set([...prev].filter((x) => x !== id));
      }
      return new Set([...prev, id]);
    });
  };

  return (
    <div className="flex flex-col gap-3">
      <OrgSearchBox
        root={root}
        onSelectOrgNode={handleSelectOrgNode}
        onSelectUser={handleSelectUser}
      />
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
    </div>
  );
}