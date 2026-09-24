// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

/**
 * CostTrendCard — 합계 + 팀별 추이 멀티 시리즈 조립·토글 검증.
 *
 * 못박는 것:
 *  - 날짜 축 = 합계 + 팀 포인트의 합집합(정렬). 어느 시리즈든 없는 날짜는 null
 *    (0 으로 접으면 "그날 비용 0" 이라는 거짓 사실).
 *  - 기본 선택 = 비용 상위 5개 팀, 나머지는 'other' 합산. 칩 토글로 켠 팀은
 *    개별 시리즈, 꺼진 팀은 'other' 에 합쳐진다(합계와 어긋나지 않는다).
 *  - 합계 시리즈는 trends 값 그대로 — 팀 시리즈 합산으로 재계산하지 않는다.
 *  - 팀이 없으면 팀/기타 시리즈·칩·범례가 나오지 않는다(단일 합계 카드 유지).
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { vi } from 'vitest';
import {
  buildTrendChartData,
  CostTrendCard,
  defaultSelectedTeamIds,
} from '@/components/dashboard/CostTrendCard';

// next-intl 은 provider 없이는 throw — 키를 그대로 돌려주는 대역으로 대체.
vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}));

const pt = (date: string, cost: number) => ({ date, cost_usd: cost, requests: 1 });
const team = (id: string, name: string, points: [string, number][], dept?: string) => ({
  team: name,
  team_id: id,
  dept_name: dept ?? null,
  points: points.map(([d, c]) => pt(d, c)),
});

describe('buildTrendChartData', () => {
  it('합계만 있고 팀이 없으면 단일 시리즈다 — 범례/팀 키가 나오지 않는다', () => {
    const r = buildTrendChartData([pt('2026-09-01', 10), pt('2026-09-02', 20)], []);
    expect(r.hasTeamSeries).toBe(false);
    expect(r.showOther).toBe(false);
    expect(r.series).toHaveLength(0);
    expect(r.allTeams).toHaveLength(0);
    expect(r.data.map((d) => d.total)).toEqual([10, 20]);
    expect(r.data[0].other).toBeUndefined();
  });

  it('부서가 있는 팀은 라벨이 "부서-팀"이다 — 부서 없으면 팀명 그대로', () => {
    const r = buildTrendChartData(
      [pt('2026-09-01', 10)],
      [
        team('a', 'Developers', [['2026-09-01', 6]], 'NDS'),
        team('b', 'Infra', [['2026-09-01', 4]]),
      ],
    );
    expect(r.series.map((s) => s.name)).toEqual(['NDS-Developers', 'Infra']);
  });

  it('팀이 하나면 합계 + 그 팀 시리즈다', () => {
    const r = buildTrendChartData(
      [pt('2026-09-01', 10)],
      [team('a', 'Developers', [['2026-09-01', 10]])],
    );
    expect(r.hasTeamSeries).toBe(true);
    expect(r.series.map((s) => s.name)).toEqual(['Developers']);
    expect(r.data[0].team_a).toBe(10);
    expect(r.showOther).toBe(false);
  });

  it('팀별로 다른 날짜에 데이터가 있어도 날짜 축은 합집합이다 — 빈 날짜는 null', () => {
    const r = buildTrendChartData(
      [pt('2026-09-01', 10), pt('2026-09-02', 4), pt('2026-09-03', 6)],
      [
        team('a', 'A팀', [['2026-09-01', 10]]),
        team('b', 'B팀', [['2026-09-02', 4], ['2026-09-03', 6]]),
      ],
    );
    expect(r.data.map((d) => d.label)).toEqual(['09-01', '09-02', '09-03']);
    expect(r.data.map((d) => d.team_a)).toEqual([10, null, null]);
    expect(r.data.map((d) => d.team_b)).toEqual([null, 4, 6]);
    expect(r.data.map((d) => d.total)).toEqual([10, 4, 6]);
  });

  it('기본 선택은 비용 상위 5개 — 나머지는 other 합산이다', () => {
    const mk = (n: number) => team(`t${n}`, `팀${n}`, [['2026-09-01', (7 - n) * 10]]);
    const r = buildTrendChartData([pt('2026-09-01', 210)], [mk(1), mk(2), mk(3), mk(4), mk(5), mk(6)]);
    expect(r.series.map((s) => s.name)).toEqual(['팀1', '팀2', '팀3', '팀4', '팀5']);
    expect(r.allTeams).toHaveLength(6); // 칩은 전체 팀을 보여준다
    expect(r.showOther).toBe(true);
    expect(r.data[0].other).toBe(10); // 팀6 합산
  });

  it('other 는 날짜별 합산이다 — 꺼진 팀들의 같은 날 비용을 더한다', () => {
    const teams = Array.from({ length: 7 }, (_, i) =>
      team(`t${i}`, `팀${i}`, [
        ['2026-09-01', (10 - i) * 5],
        ['2026-09-02', i * 5],
      ]),
    );
    const r = buildTrendChartData([], teams);
    // 기본 선택 t0..t4, 꺼짐: t5(25/25), t6(20/30)
    expect(r.data.map((d) => d.other)).toEqual([45, 55]);
  });

  it('선택을 바꾸면 개별 시리즈와 other 가 함께 바뀐다', () => {
    const teams = [
      team('a', 'A', [['2026-09-01', 30]]),
      team('b', 'B', [['2026-09-01', 20]]),
      team('c', 'C', [['2026-09-01', 10]]),
    ];
    // A만 선택 → B+C 가 other
    const r = buildTrendChartData([], teams, new Set(['a']));
    expect(r.series.map((s) => s.teamId)).toEqual(['a']);
    expect(r.data[0].other).toBe(30);
    // 전부 선택 → other 소멸
    const all = buildTrendChartData([], teams, new Set(['a', 'b', 'c']));
    expect(all.showOther).toBe(false);
    expect(all.data[0].other).toBeUndefined();
  });

  it('합계는 trends 값을 그대로 쓴다 — 팀 합산으로 재계산하지 않는다', () => {
    const r = buildTrendChartData(
      [pt('2026-09-01', 35)],
      [team('a', 'A', [['2026-09-01', 10]]), team('b', 'B', [['2026-09-01', 20]])],
    );
    expect(r.data[0].total).toBe(35);
  });

  it('개별 팀 시리즈는 서로 다른 색과 선형을 갖는다', () => {
    const teams = Array.from({ length: 5 }, (_, i) => team(`t${i}`, `팀${i}`, [['2026-09-01', 10 - i]]));
    const r = buildTrendChartData([], teams);
    const colors = r.series.map((s) => s.color);
    const dashes = r.series.map((s) => s.dash);
    expect(new Set(colors).size).toBe(5);
    expect(new Set(dashes).size).toBe(5);
  });

  it('빈 입력이면 빈 데이터다 — 렌더는 빈 상태로 간다', () => {
    const r = buildTrendChartData([], []);
    expect(r.data).toEqual([]);
    expect(r.hasTeamSeries).toBe(false);
  });
});

describe('defaultSelectedTeamIds', () => {
  it('비용 상위 5개 팀 id 를 돌려준다', () => {
    const teams = Array.from({ length: 7 }, (_, i) => team(`t${i}`, `팀${i}`, [['2026-09-01', 10 - i]]));
    expect([...defaultSelectedTeamIds(teams)].sort()).toEqual(['t0', 't1', 't2', 't3', 't4']);
  });
});

describe('CostTrendCard', () => {
  it('데이터가 없으면 빈 상태 문구를 표시한다', () => {
    render(<CostTrendCard trends={[]} />);
    expect(screen.getByText('costTrendEmpty')).toBeInTheDocument();
  });

  it('팀이 있으면 칩이 렌더되고 기본은 상위 팀만 켜져 있다', () => {
    const teams = Array.from({ length: 6 }, (_, i) =>
      team(`t${i}`, `팀${i}`, [['2026-09-01', 100 - i * 10]]),
    );
    render(<CostTrendCard trends={[pt('2026-09-01', 350)]} trendsByTeam={teams} />);
    const chips = screen.getAllByRole('button');
    expect(chips).toHaveLength(6);
    // 팀5(6위)는 기본 선택 밖 → 꺼짐
    expect(screen.getByRole('button', { name: '팀5' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: '팀0' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('칩을 누르면 그 팀의 표시가 토글된다', () => {
    const teams = Array.from({ length: 6 }, (_, i) =>
      team(`t${i}`, `팀${i}`, [['2026-09-01', 100 - i * 10]]),
    );
    render(<CostTrendCard trends={[pt('2026-09-01', 350)]} trendsByTeam={teams} />);
    const chip5 = screen.getByRole('button', { name: '팀5' });
    fireEvent.click(chip5);
    expect(chip5).toHaveAttribute('aria-pressed', 'true');
    const chip0 = screen.getByRole('button', { name: '팀0' });
    fireEvent.click(chip0);
    expect(chip0).toHaveAttribute('aria-pressed', 'false');
  });

  it('팀이 없으면 칩이 나오지 않는다', () => {
    render(<CostTrendCard trends={[pt('2026-09-01', 10)]} />);
    expect(screen.queryByRole('button')).toBeNull();
  });
});
