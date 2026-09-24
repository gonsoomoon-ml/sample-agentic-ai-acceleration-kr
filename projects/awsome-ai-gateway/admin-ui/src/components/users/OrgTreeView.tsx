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
// 선택 노드도 sessionStorage 에 지속한다 — 정책 저장 후의 router.refresh /
// revalidatePath 리페치가 loading.tsx Suspense 를 거쳐 이 컴포넌트를 리마운트하면
// useState 인 selectedNode 가 통째로 날아간다(/apps 의 ?app= 사건과 같은 결함).
const SELECTED_NODE_STORAGE_KEY = 'users:orgtree:selectedNode';

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
  const selectedRestoredRef = useRef(false);

  // 선택 변경 시 persist — 리마운트 복원의 소스.
  useEffect(() => {
    if (!selectedRestoredRef.current) return; // 복원 effect 보다 먼저 쓰지 않는다
    try {
      if (selectedNode) {
        sessionStorage.setItem(SELECTED_NODE_STORAGE_KEY, JSON.stringify(selectedNode));
      } else {
        sessionStorage.removeItem(SELECTED_NODE_STORAGE_KEY);
      }
    } catch {
      // 무시
    }
  }, [selectedNode]);

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
    // 선택 노드 복원(마운트 1회) — 저장된 노드는 id 가 같으므로 아래 resync
    // effect 가 새 트리의 동일 노드로 교체한다. 트리에 없으면 검색 합성 노드로
    // 저장된 것 — 저장분 그대로 표시.
    if (!selectedRestoredRef.current) {
      selectedRestoredRef.current = true;
      try {
        const raw = sessionStorage.getItem(SELECTED_NODE_STORAGE_KEY);
        if (raw) {
          const n = JSON.parse(raw) as OrgTreeNode;
          if (n && typeof n.id === 'string' && typeof n.type === 'string') {
            setSelectedNode(n);
          }
        }
      } catch {
        // 파싱 실패는 무시 — 선택 없음으로 시작
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
      {/* shrink-0: 우측 패널의 넓은 내용(유효 정책 매트릭스 등)이 트리 열을
          0폭까지 수축시키는 것을 막는다. 우측은 min-w-0 으로 수축을 허용해
          내부 overflow-x-auto 가 대신 동작한다. */}
      <div className="w-72 shrink-0 border-r overflow-y-auto">
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
      <div className="flex-1 min-w-0 p-6">
        <OrgDetailPanel node={selectedNode} />
      </div>
      </div>
    </div>
  );
}