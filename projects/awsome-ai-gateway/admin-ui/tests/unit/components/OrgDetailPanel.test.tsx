// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

/**
 * 부서/조직 상세가 **팀 수 자리에 사람 수를 렌더하지 않는지**.
 *
 * 원래 증상: 20팀 × 50명 부서가 "팀 1000개" 로 표시됐다. 서버는
 * `meta.member_count` 에 하위 팀들의 사용자 수 합을 넣는데 이 패널이 그 값을 팀
 * 수로 읽었기 때문이다(필드 하나가 노드 타입마다 다른 의미를 가졌다).
 *
 * ⚠️ 백엔드 단정만으로는 이 증상을 증명할 수 없다. 서버가 `team_count` 를 올바르게
 *    주더라도 패널이 계속 `member_count` 를 읽으면 화면의 숫자는 그대로 틀린다.
 *    그래서 **렌더 결과**를 본다.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { OrgDetailPanel } from '@/components/users/OrgDetailPanel';
import type { OrgTreeNode } from '@/types/entities';

// next-intl 을 실제 메시지 대신 키 기반 스텁으로 — 이 테스트의 관심사는 **어느 값이
// 어느 라벨에 붙는가** 이고, 번역 문구가 바뀌어도 그 계약은 유지돼야 한다.
vi.mock('next-intl', () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params && 'count' in params ? `${key}=${params.count}` : key,
}));

function userNode(name: string): OrgTreeNode {
  return {
    id: `u-${name}`,
    name,
    type: 'USER',
    children: [],
    meta: {
      member_count: null,
      team_count: null,
      leader_name: null,
      leader_user_id: null,
      email: `${name}@example.com`,
      role: 'DEVELOPER',
      team_name: 'T',
    },
  } as OrgTreeNode;
}

function teamNode(name: string, memberCount: number): OrgTreeNode {
  return {
    id: `t-${name}`,
    name,
    type: 'TEAM',
    children: Array.from({ length: memberCount }, (_, i) => userNode(`${name}-${i}`)),
    meta: {
      member_count: memberCount,
      team_count: null,
      leader_name: 'Lead',
      email: 'lead@example.com',
      role: null,
      team_name: null,
    },
  } as OrgTreeNode;
}

/** 20팀 × 50명 — 원래 버그가 드러난 정확한 형상. */
function departmentNode(teams: number, perTeam: number): OrgTreeNode {
  const children = Array.from({ length: teams }, (_, i) => teamNode(`team-${i}`, perTeam));
  return {
    id: 'd-1',
    name: 'Dept A',
    type: 'DEPARTMENT',
    children,
    meta: {
      member_count: teams * perTeam,
      team_count: teams,
      leader_name: null,
      leader_user_id: null,
      email: null,
      role: null,
      team_name: null,
    },
  } as OrgTreeNode;
}

describe('OrgDetailPanel — DEPARTMENT 카운트', () => {
  it('팀 수 자리에 팀 수가 온다 (사람 수가 아니다)', () => {
    render(<OrgDetailPanel node={departmentNode(20, 50)} />);
    // teamCount 라벨 뒤의 값은 20 이어야 한다. 1000 이면 원래 버그다.
    expect(screen.getByText('countSuffix=20')).toBeInTheDocument();
    expect(screen.queryByText('countSuffix=1000')).not.toBeInTheDocument();
  });

  it('사람 수를 별도 줄로 함께 보여준다', () => {
    render(<OrgDetailPanel node={departmentNode(20, 50)} />);
    expect(screen.getByText('teamCount')).toBeInTheDocument();
    expect(screen.getByText('memberCount')).toBeInTheDocument();
    expect(screen.getByText('memberCountValue=1000')).toBeInTheDocument();
  });

  it('team_count 가 없으면 children 수로 폴백한다 (사람 수로 폴백하지 않는다)', () => {
    // 구버전 서버와 섞여 배포되는 창을 가정한다. 그때도 사람 수를 팀 수 자리에
    // 넣어서는 안 된다 — 그게 원래 버그의 형태다.
    const node = departmentNode(3, 7);
    (node.meta as { team_count: number | null }).team_count = null;
    render(<OrgDetailPanel node={node} />);
    expect(screen.getByText('countSuffix=3')).toBeInTheDocument(); // children.length
    expect(screen.queryByText('countSuffix=21')).not.toBeInTheDocument(); // 3×7 사람 수
  });

  it('member_count 가 없으면 사람 수 줄을 아예 그리지 않는다', () => {
    const node = departmentNode(4, 2);
    (node.meta as { member_count: number | null }).member_count = null;
    render(<OrgDetailPanel node={node} />);
    // "모른다" 를 0 으로 표시하면 "구성원 0명" 이라는 틀린 주장이 된다.
    expect(screen.queryByText('memberCount')).not.toBeInTheDocument();
  });
});

describe('OrgDetailPanel — ORGANIZATION 카운트', () => {
  function orgNode(): OrgTreeNode {
    const depts = [departmentNode(20, 50), departmentNode(2, 1)];
    depts[1].name = 'Dept B';
    depts[1].id = 'd-2';
    return {
      id: 'o-1',
      name: 'Org',
      type: 'ORGANIZATION',
      children: depts,
      meta: {
        member_count: 20 * 50 + 2 * 1,
        team_count: 22,
        leader_name: null,
        leader_user_id: null,
        email: null,
        role: null,
        team_name: null,
      },
    } as OrgTreeNode;
  }

  it('부서 수·팀 수·사람 수 세 값을 서로 다른 라벨로 보여준다', () => {
    render(<OrgDetailPanel node={orgNode()} />);
    expect(screen.getByText('departmentCount')).toBeInTheDocument();
    expect(screen.getByText('countSuffix=2')).toBeInTheDocument(); // 부서 2개
    expect(screen.getByText('teamCount')).toBeInTheDocument();
    expect(screen.getByText('countSuffix=22')).toBeInTheDocument(); // 팀 22개
    expect(screen.getByText('memberCountValue=1002')).toBeInTheDocument(); // 사람 1002명
  });

  it('세 값이 서로 섞이지 않는다', () => {
    render(<OrgDetailPanel node={orgNode()} />);
    // 부서 수 라벨에 팀 수(22)나 사람 수(1002)가 붙으면 실패한다.
    expect(screen.queryByText('memberCountValue=22')).not.toBeInTheDocument();
    expect(screen.queryByText('countSuffix=1002')).not.toBeInTheDocument();
  });
});
