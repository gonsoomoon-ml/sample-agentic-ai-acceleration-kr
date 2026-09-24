// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { OrgTreeNode } from '@/types/entities';

// next-intl needs a client provider at runtime; stub useTranslations to echo keys.
vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}));

const searchUsersAction = vi.fn();
vi.mock('@/lib/actions/users', () => ({
  searchUsersAction: (...a: unknown[]) => searchUsersAction(...a),
}));

import { OrgSearchBox } from '@/components/users/OrgSearchBox';

const meta = (memberCount: number | null = null) => ({
  member_count: memberCount,
  team_count: null,
  leader_name: null,
  leader_user_id: null,
  email: null,
  role: null,
  team_name: null,
});

const root: OrgTreeNode = {
  id: 'org',
  name: 'Org',
  type: 'ORGANIZATION',
  meta: meta(5),
  children: [
    {
      id: 'dept',
      name: 'Platform Dept',
      type: 'DEPARTMENT',
      meta: meta(5),
      children: [
        { id: 'team-a', name: 'Alpha Team', type: 'TEAM', children: [], meta: meta(3) },
        { id: 'team-b', name: 'Beta Team', type: 'TEAM', children: [], meta: meta(2) },
      ],
    },
  ],
};

const aliceUser = {
  id: 'u1',
  email: 'alice@test.com',
  display_name: 'Alice',
  role: 'DEVELOPER' as const,
  team_id: 'team-a',
  team_name: 'Alpha Team',
};

function setup(overrides: Partial<React.ComponentProps<typeof OrgSearchBox>> = {}) {
  const onSelectOrgNode = vi.fn();
  const onSelectUser = vi.fn();
  render(
    <OrgSearchBox
      root={root}
      onSelectOrgNode={onSelectOrgNode}
      onSelectUser={onSelectUser}
      {...overrides}
    />,
  );
  return { onSelectOrgNode, onSelectUser };
}

beforeEach(() => {
  searchUsersAction.mockReset();
  searchUsersAction.mockResolvedValue({ success: true, data: { items: [], truncated: false } });
});

describe('OrgSearchBox', () => {
  it('matches team names from the tree payload without calling the server', async () => {
    setup();
    await userEvent.type(screen.getByRole('combobox'), 'Alpha');
    expect(await screen.findByText('Alpha Team')).toBeInTheDocument();
    // 팀/부서는 트리 payload 로 즉시 매칭 — 사용자 검색만 서버로 간다.
    expect(screen.queryByText('Beta Team')).not.toBeInTheDocument();
  });

  it('does not query the server below the minimum length', async () => {
    setup();
    await userEvent.type(screen.getByRole('combobox'), 'A');
    // debounce 를 넘겨도 호출되지 않아야 한다.
    await new Promise((r) => setTimeout(r, 400));
    expect(searchUsersAction).not.toHaveBeenCalled();
    expect(screen.getByText('minLength')).toBeInTheDocument();
  });

  it('debounces the user search into a single call', async () => {
    setup();
    await userEvent.type(screen.getByRole('combobox'), 'alice');
    await waitFor(() => expect(searchUsersAction).toHaveBeenCalled());
    expect(searchUsersAction).toHaveBeenCalledTimes(1);
    expect(searchUsersAction).toHaveBeenCalledWith('alice');
  });

  it('shows user results returned by the server', async () => {
    searchUsersAction.mockResolvedValue({
      success: true,
      data: { items: [aliceUser], truncated: false },
    });
    setup();
    await userEvent.type(screen.getByRole('combobox'), 'alice');
    expect(await screen.findByText('Alice')).toBeInTheDocument();
    expect(screen.getByText(/alice@test\.com/)).toBeInTheDocument();
  });

  it('calls onSelectUser with the picked user', async () => {
    searchUsersAction.mockResolvedValue({
      success: true,
      data: { items: [aliceUser], truncated: false },
    });
    const { onSelectUser } = setup();
    await userEvent.type(screen.getByRole('combobox'), 'alice');
    await userEvent.click(await screen.findByText('Alice'));
    expect(onSelectUser).toHaveBeenCalledWith(aliceUser);
  });

  it('calls onSelectOrgNode with ancestor ids for tree expansion', async () => {
    const { onSelectOrgNode } = setup();
    await userEvent.type(screen.getByRole('combobox'), 'Alpha');
    await userEvent.click(await screen.findByText('Alpha Team'));
    expect(onSelectOrgNode).toHaveBeenCalledTimes(1);
    const match = onSelectOrgNode.mock.calls[0][0];
    expect(match.node.id).toBe('team-a');
    expect(match.ancestorIds).toEqual(['org', 'dept']);
  });

  it('selects the highlighted item on Enter', async () => {
    const { onSelectOrgNode } = setup();
    const input = screen.getByRole('combobox');
    await userEvent.type(input, 'Team');
    await screen.findByText('Alpha Team');
    await userEvent.keyboard('{Enter}');
    // 첫 항목이 기본 하이라이트 → Enter 로 바로 선택된다.
    expect(onSelectOrgNode).toHaveBeenCalledTimes(1);
  });

  it('moves the highlight with ArrowDown before selecting', async () => {
    const { onSelectOrgNode } = setup();
    await userEvent.type(screen.getByRole('combobox'), 'Team');
    await screen.findByText('Alpha Team');
    await userEvent.keyboard('{ArrowDown}{Enter}');
    expect(onSelectOrgNode.mock.calls[0][0].node.id).toBe('team-b');
  });

  it('closes the dropdown on Escape without selecting', async () => {
    const { onSelectOrgNode } = setup();
    await userEvent.type(screen.getByRole('combobox'), 'Alpha');
    await screen.findByText('Alpha Team');
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByText('Alpha Team')).not.toBeInTheDocument();
    expect(onSelectOrgNode).not.toHaveBeenCalled();
  });

  it('shows an error message when the user search fails', async () => {
    searchUsersAction.mockResolvedValue({ success: false, error: 'boom' });
    setup();
    await userEvent.type(screen.getByRole('combobox'), 'alice');
    expect(await screen.findByText('failed')).toBeInTheDocument();
  });

  it('shows a no-results message when nothing matches', async () => {
    setup();
    await userEvent.type(screen.getByRole('combobox'), 'zzzz');
    expect(await screen.findByText('noResults')).toBeInTheDocument();
  });

  it('clears the search when the clear button is pressed', async () => {
    setup();
    const input = screen.getByRole('combobox');
    await userEvent.type(input, 'Alpha');
    await screen.findByText('Alpha Team');
    await userEvent.click(screen.getByLabelText('clear'));
    expect(input).toHaveValue('');
    expect(screen.queryByText('Alpha Team')).not.toBeInTheDocument();
  });

  it('warns when server results were truncated', async () => {
    searchUsersAction.mockResolvedValue({
      success: true,
      data: { items: [aliceUser], truncated: true },
    });
    setup();
    await userEvent.type(screen.getByRole('combobox'), 'alice');
    expect(await screen.findByText('truncated')).toBeInTheDocument();
  });

  it('ignores a stale response that arrives after a newer search', async () => {
    // 첫 요청은 느리게, 두 번째는 즉시 → 느린 응답이 최신 결과를 덮어써선 안 된다.
    const stale = {
      success: true,
      data: {
        items: [{ ...aliceUser, id: 'stale', display_name: 'StaleUser' }],
        truncated: false,
      },
    };
    const fresh = {
      success: true,
      data: {
        items: [{ ...aliceUser, id: 'fresh', display_name: 'FreshUser' }],
        truncated: false,
      },
    };
    searchUsersAction
      .mockImplementationOnce(
        () => new Promise((resolve) => setTimeout(() => resolve(stale), 300)),
      )
      .mockResolvedValueOnce(fresh);

    setup();
    const input = screen.getByRole('combobox');
    await userEvent.type(input, 'alic');
    // 첫 debounce 가 발화하도록 대기한 뒤 검색어를 바꿔 두 번째 요청을 만든다.
    await new Promise((r) => setTimeout(r, 300));
    await userEvent.type(input, 'e');

    expect(await screen.findByText('FreshUser')).toBeInTheDocument();
    // 느린 첫 응답이 도착할 시간을 준다.
    await new Promise((r) => setTimeout(r, 400));
    expect(screen.queryByText('StaleUser')).not.toBeInTheDocument();
    expect(screen.getByText('FreshUser')).toBeInTheDocument();
  });
});
