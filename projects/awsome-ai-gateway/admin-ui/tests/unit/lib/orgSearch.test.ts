// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

import { describe, it, expect } from 'vitest';
import type { OrgTreeNode } from '@/types/entities';
import { searchOrgNodes, findTeamExpandPath } from '@/lib/utils/orgSearch';

const meta = (memberCount: number | null = null) => ({
  member_count: memberCount,
  team_count: null,
  leader_name: null,
  leader_user_id: null,
  email: null,
  role: null,
  team_name: null,
});

// org
//  ├ DS부문
//  │   ├ SW개발팀 (5)
//  │   └ 차세대SW팀 (3)
//  └ SW부문
//      └ 플랫폼팀 (2)
const root: OrgTreeNode = {
  id: 'org',
  name: '전사',
  type: 'ORGANIZATION',
  meta: meta(10),
  children: [
    {
      id: 'dept-ds',
      name: 'DS부문',
      type: 'DEPARTMENT',
      meta: meta(8),
      children: [
        { id: 'team-sw', name: 'SW개발팀', type: 'TEAM', children: [], meta: meta(5) },
        { id: 'team-next', name: '차세대SW팀', type: 'TEAM', children: [], meta: meta(3) },
      ],
    },
    {
      id: 'dept-sw',
      name: 'SW부문',
      type: 'DEPARTMENT',
      meta: meta(2),
      children: [
        { id: 'team-platform', name: '플랫폼팀', type: 'TEAM', children: [], meta: meta(2) },
      ],
    },
  ],
};

describe('searchOrgNodes', () => {
  it('matches team names by substring', () => {
    const { matches } = searchOrgNodes(root, '플랫폼');
    expect(matches.map((m) => m.node.id)).toEqual(['team-platform']);
  });

  it('matches department names', () => {
    const { matches } = searchOrgNodes(root, 'DS부문');
    expect(matches.map((m) => m.node.id)).toEqual(['dept-ds']);
  });

  it('ranks prefix matches ahead of mid-string matches', () => {
    // "SW" → SW개발팀/SW부문 은 prefix, 차세대SW팀 은 중간 일치.
    const { matches } = searchOrgNodes(root, 'SW');
    const lastId = matches[matches.length - 1].node.id;
    expect(lastId).toBe('team-next');
    expect(matches.map((m) => m.node.id)).toContain('dept-sw');
    expect(matches.map((m) => m.node.id)).toContain('team-sw');
  });

  it('is case-insensitive', () => {
    const { matches } = searchOrgNodes(root, 'sw개발');
    expect(matches.map((m) => m.node.id)).toEqual(['team-sw']);
  });

  it('excludes the ORGANIZATION root — there is nowhere to navigate to', () => {
    const { matches } = searchOrgNodes(root, '전사');
    expect(matches).toEqual([]);
  });

  it('returns ancestor ids so the tree can expand to reveal the node', () => {
    const { matches } = searchOrgNodes(root, 'SW개발팀');
    expect(matches[0].ancestorIds).toEqual(['org', 'dept-ds']);
    expect(matches[0].parentName).toBe('DS부문');
  });

  it('returns nothing below the minimum length', () => {
    expect(searchOrgNodes(root, 'S').matches).toEqual([]);
    expect(searchOrgNodes(root, '').matches).toEqual([]);
    expect(searchOrgNodes(root, '  ').matches).toEqual([]);
  });

  it('returns nothing for a null tree', () => {
    expect(searchOrgNodes(null, '플랫폼').matches).toEqual([]);
  });

  it('flags truncation and caps the result at the limit', () => {
    // "SW" 는 SW개발팀/SW부문/차세대SW팀 3건 매칭 → limit 2 로 잘린다.
    const { matches, truncated } = searchOrgNodes(root, 'SW', 2);
    expect(matches).toHaveLength(2);
    expect(truncated).toBe(true);
  });

  it('finds a child match even when the parent does not match', () => {
    // 플랫폼팀 의 부모(SW부문)는 "플랫폼" 을 포함하지 않는다.
    const { matches } = searchOrgNodes(root, '플랫폼팀');
    expect(matches).toHaveLength(1);
  });
});

describe('findTeamExpandPath', () => {
  it('includes the team itself so its members get lazy-loaded', () => {
    // 사용자 노드를 드러내려면 조상뿐 아니라 팀 자신도 펼쳐야 한다.
    expect(findTeamExpandPath(root, 'team-sw')).toEqual(['org', 'dept-ds', 'team-sw']);
  });

  it('returns null for a team not present in the tree', () => {
    // 멤버 0명인 팀은 트리에서 숨겨진다 → 검색 결과의 팀이 트리에 없을 수 있다.
    expect(findTeamExpandPath(root, 'team-ghost')).toBeNull();
  });

  it('returns null for a null tree', () => {
    expect(findTeamExpandPath(null, 'team-sw')).toBeNull();
  });

  it('does not match a department id', () => {
    expect(findTeamExpandPath(root, 'dept-ds')).toBeNull();
  });
});
