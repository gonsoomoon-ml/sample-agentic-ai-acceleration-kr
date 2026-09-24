'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

import type { ModelListItem } from '@/types/entities';

export interface DowngradeDiagramRule {
  from_model_alias: string;
  to_model_alias: string;
  threshold_pct: string | number;
  /** 읽기 전용 뷰에서 edgeTag 라벨링에 쓰는 스코프(TEAM/USER 등). */
  scope?: string;
}

/**
 * 다운그레이드 규칙(from→to)을 DAG로 시각화 — 노드=모델(output 단가 표기),
 * 엣지=규칙(% 라벨). 노드 깊이는 relaxation으로 계산: 어떤 규칙의 to 인 노드는
 * max(from 깊이)+1 레이어에 놓여 a→b→c 체인도 세 열로 정렬된다.
 * edgeTag 를 넘기면 % 라벨 뒤에 스코프 등의 꼬리표를 붙인다 (읽기 전용 카드용).
 */
export function DowngradeDiagram({
  rules,
  models,
  formatOutPrice,
  edgeTag,
}: {
  rules: DowngradeDiagramRule[];
  models: ModelListItem[];
  formatOutPrice: (_m: ModelListItem) => string;
  edgeTag?: (_rule: DowngradeDiagramRule) => string | null;
}) {
  // from/to 가 비어 있는 규칙(작성 중인 새 행)은 레이아웃·렌더 모두에서 제외한다 —
  // 포함하면 빈 alias 가 유령 노드 열을 만들고 엣지가 화면 밖으로 길게 뻗는다.
  const visibleRules = rules.filter(
    (r) => r.from_model_alias && r.to_model_alias,
  );
  // 완성된 규칙이 하나도 없으면 빈 프레임 대신 아무것도 그리지 않는다 —
  // 편집 화면에서 "규칙 추가" 직후의 빈 행이 유령 다이어그램을 만들지 않게.
  if (visibleRules.length === 0) return null;

  const depth = new Map<string, number>();
  for (const r of visibleRules) {
    if (!depth.has(r.from_model_alias)) depth.set(r.from_model_alias, 0);
  }
  for (let pass = 0; pass < visibleRules.length; pass++) {
    for (const r of visibleRules) {
      const d = (depth.get(r.from_model_alias) ?? 0) + 1;
      if ((depth.get(r.to_model_alias) ?? -1) < d) depth.set(r.to_model_alias, d);
    }
  }

  const maxDepth = Math.max(0, ...depth.values());
  const layerCount = maxDepth + 1;
  const priceOf = (alias: string) =>
    models.find(m => m.alias === alias)?.output_price_per_1k;

  // 노드 색은 output 단가의 상대 위치 — 비쌀수록 초록, 쌀수록 빨강 계열.
  const TONES = [
    'bg-rose-500/10 border-rose-500/40 text-rose-700 dark:text-rose-300',
    'bg-orange-500/10 border-orange-500/40 text-orange-700 dark:text-orange-300',
    'bg-amber-500/10 border-amber-500/40 text-amber-700 dark:text-amber-300',
    'bg-lime-500/10 border-lime-500/40 text-lime-700 dark:text-lime-300',
    'bg-emerald-500/10 border-emerald-500/40 text-emerald-700 dark:text-emerald-300',
  ];
  const prices = [...depth.keys()]
    .map(a => priceOf(a))
    .filter((p): p is number => p != null);
  const minP = Math.min(...prices);
  const maxP = Math.max(...prices);
  const toneOf = (alias: string) => {
    const p = priceOf(alias);
    if (p == null || prices.length === 0) return 'bg-muted/40 border-border/60';
    const t = maxP > minP ? (p - minP) / (maxP - minP) : 0.5;
    return TONES[Math.round(t * (TONES.length - 1))];
  };

  const layers: string[][] = Array.from({ length: layerCount }, () => []);
  for (const [alias, d] of depth) layers[d].push(alias);
  for (const l of layers) l.sort((a, b) => (priceOf(b) ?? 0) - (priceOf(a) ?? 0));

  const pos = new Map<string, { l: number; i: number; n: number }>();
  layers.forEach((nodes, l) =>
    nodes.forEach((a, i) => pos.set(a, { l, i, n: nodes.length })),
  );

  // 규칙별 엣지 색 — 겹쳐 그려진 곡선과 % 라벨이 어느 규칙의 것인지 구분할 수
  // 있게 path·marker·라벨 칩이 같은 색을 공유한다. 규칙 수가 팔레트를 넘으면
  // 순환한다(이완적으로도 6개를 넘는 규칙 묶음은 드물다).
  const EDGE_COLORS = [
    {
      stroke: 'stroke-sky-500',
      fill: 'fill-sky-500',
      chip: 'border-sky-500/50 bg-sky-500/10 text-sky-700 dark:text-sky-300',
    },
    {
      stroke: 'stroke-violet-500',
      fill: 'fill-violet-500',
      chip: 'border-violet-500/50 bg-violet-500/10 text-violet-700 dark:text-violet-300',
    },
    {
      stroke: 'stroke-rose-500',
      fill: 'fill-rose-500',
      chip: 'border-rose-500/50 bg-rose-500/10 text-rose-700 dark:text-rose-300',
    },
    {
      stroke: 'stroke-amber-600',
      fill: 'fill-amber-600',
      chip: 'border-amber-600/50 bg-amber-500/10 text-amber-700 dark:text-amber-300',
    },
    {
      stroke: 'stroke-emerald-500',
      fill: 'fill-emerald-500',
      chip: 'border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
    },
    {
      stroke: 'stroke-fuchsia-500',
      fill: 'fill-fuchsia-500',
      chip: 'border-fuchsia-500/50 bg-fuchsia-500/10 text-fuchsia-700 dark:text-fuchsia-300',
    },
  ];
  const edgeColor = (idx: number) => EDGE_COLORS[idx % EDGE_COLORS.length];

  // 칩은 컬럼 폭의 86% (중앙 정렬) — 엣지는 칩의 좌/우 끝에 닿도록 보정.
  const chipPad = (100 / layerCount) * 0.07;
  const edgeX1 = (l: number) => ((l + 1) / layerCount) * 100 - chipPad;
  const edgeX2 = (l: number) => (l / layerCount) * 100 + chipPad;
  const nodeY = (i: number, n: number) => ((i + 0.5) / n) * 100;
  const height = Math.max(1, ...layers.map(l => l.length)) * 52;

  return (
    <div className="rounded-xl border border-border/60 bg-muted/20 px-3 py-3">
      <div className="relative" style={{ height }}>
        {layers.map((nodes, l) => (
          <div
            key={l}
            className="absolute top-0 bottom-0 flex flex-col justify-around"
            style={{ left: `${(l / layerCount) * 100}%`, width: `${100 / layerCount}%` }}
          >
            {nodes.map(alias => {
              const m = models.find(mm => mm.alias === alias);
              return (
                <div
                  key={alias}
                  className={`mx-auto w-[86%] rounded-lg border px-2 py-1 text-center shadow-sm ${toneOf(alias)}`}
                >
                  <div className="truncate font-mono text-[11px] font-medium">{alias}</div>
                  <div className="text-[9px] tabular-nums opacity-80">
                    {m ? formatOutPrice(m) : '—'}
                  </div>
                </div>
              );
            })}
          </div>
        ))}

        <svg
          className="absolute inset-0 h-full w-full"
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <defs>
            {EDGE_COLORS.map((c, i) => (
              <marker
                key={i}
                id={`dg-arrow-${i}`}
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="5"
                markerHeight="5"
                orient="auto-start-reverse"
              >
                <path d="M0,0 L10,5 L0,10 z" className={c.fill} />
              </marker>
            ))}
          </defs>
          {visibleRules.map((r, idx) => {
            const a = pos.get(r.from_model_alias);
            const b = pos.get(r.to_model_alias);
            if (!a || !b) return null;
            const x1 = edgeX1(a.l);
            const y1 = nodeY(a.i, a.n);
            const x2 = edgeX2(b.l);
            const y2 = nodeY(b.i, b.n);
            const dx = Math.max((x2 - x1) * 0.5, 4);
            // 규칙별 수직 오목(bow) — 같은 구간을 지나는 엣지가 완전히 겹치면
            // 어느 선이 어느 규칙인지 읽을 수 없다. 제어점을 인덱스만큼 벌려
            // 곡선을 팬처럼 펼치고, 라벨은 휘어진 곡선 위의 실제 중점에 둔다.
            const bow = ((idx % 5) - 2) * 3.5; // -7, -3.5, 0, +3.5, +7 (viewBox %)
            return (
              <path
                key={idx}
                d={`M ${x1} ${y1} C ${x1 + dx} ${y1 + bow}, ${x2 - dx} ${y2 + bow}, ${x2} ${y2}`}
                fill="none"
                strokeWidth="1.5"
                vectorEffect="non-scaling-stroke"
                className={edgeColor(idx).stroke}
                markerEnd={`url(#dg-arrow-${idx % EDGE_COLORS.length})`}
              />
            );
          })}
        </svg>

        {(() => {
          // 라벨 위치 = 곡선의 t=0.5 지점(중점 + 0.75·bow). 같은 칼럼 사이
          // 공간(mx 가 가까움)에 몰린 라벨이 세로로 겹치면 아래로 밀어 펼친다 —
          // 색상이 라벨↔엣지를 이어주므로 라벨이 선에서 조금 떨어져도 읽힌다.
          const labelPts = visibleRules
            .map((r, idx) => {
              const a = pos.get(r.from_model_alias);
              const b = pos.get(r.to_model_alias);
              if (!a || !b) return null;
              const bow = ((idx % 5) - 2) * 3.5;
              return {
                idx,
                mx: (edgeX1(a.l) + edgeX2(b.l)) / 2,
                my: (nodeY(a.i, a.n) + nodeY(b.i, b.n)) / 2 + bow * 0.75,
              };
            })
            .filter((p): p is { idx: number; mx: number; my: number } => p != null)
            .sort((x, y) => x.mx - y.mx || x.my - y.my);
          // 같은 칼럼 간격(mx 가 가까움)에 몰린 라벨 클러스터를 찾아, 겹침이
          // 있으면 클러스터 중심 기준으로 위아래 대칭 등간격(14%)으로 재배치한다.
          // 아래로만 미는 방식은 라벨이 많을 때 하단에 다시 쌓인다.
          for (let i = 0; i < labelPts.length; ) {
            let j = i;
            while (
              j + 1 < labelPts.length &&
              Math.abs(labelPts[j + 1].mx - labelPts[i].mx) < 12
            ) {
              j++;
            }
            const cluster = labelPts.slice(i, j + 1).sort((a, b) => a.my - b.my);
            const collides = cluster.some(
              (p, k) => k > 0 && p.my - cluster[k - 1].my < 14,
            );
            if (collides) {
              const center =
                cluster.reduce((s, p) => s + p.my, 0) / cluster.length;
              cluster.forEach((p, k) => {
                p.my = Math.min(
                  Math.max(center + (k - (cluster.length - 1) / 2) * 14, 3),
                  97,
                );
              });
            }
            i = j + 1;
          }
          return labelPts.map(({ idx, mx, my }) => {
            const r = visibleRules[idx];
            const tag = edgeTag?.(r);
            return (
              <span
                key={`t${idx}`}
                className={`absolute -translate-x-1/2 -translate-y-1/2 rounded-full border px-1.5 py-px text-[9px] font-semibold tabular-nums whitespace-nowrap ${edgeColor(idx).chip}`}
                style={{ left: `${mx}%`, top: `${my}%` }}
              >
                {r.threshold_pct}%{tag ? ` · ${tag}` : ''}
              </span>
            );
          });
        })()}
      </div>
    </div>
  );
}
