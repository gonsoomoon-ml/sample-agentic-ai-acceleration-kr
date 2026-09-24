'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.


import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { getEffectivePolicyAction } from '@/lib/actions/users';
import { DowngradeDiagram } from '@/components/common/DowngradeDiagram';
import { CLIENTS as GATEWAY_CLIENTS } from '@/lib/constants/gateway';
import type { EffectivePolicy, EffectivePolicyCell, ModelListItem } from '@/types/entities';

interface Props {
  userId: string;
  /** 부모(UserPanel)가 이미 fetch한 정책을 넘기면 재조회를 건너뛴다. */
  policy?: EffectivePolicy | null;
  /** 다운그레이드 다이어그램의 output 단가 표기용 — 없으면 단가 칸은 '—'. */
  models?: ModelListItem[];
}

/** 거부 축 id → i18n 키 매핑. */
const AXIS_KEYS = ['user_app', 'user_model', 'model_app'] as const;

/**
 * 사용자에게 실제로 적용되는 정책의 합성 읽기 전용 뷰.
 * model×app 매트릭스(어느 축에서 막혔는지) + 예산·rate limit·downgrade·web search 요약.
 * 다운그레이드 규칙은 매트릭스보다 위에 둔다 — 모델 수만큼 표가 길어져도 스크롤 없이 보이게.
 */
export function EffectivePolicyCard({ userId, policy: policyProp, models }: Props) {
  const t = useTranslations('users.effectivePolicy');
  const [fetched, setFetched] = useState<EffectivePolicy | null>(null);
  const [failed, setFailed] = useState(false);

  const policy = policyProp ?? fetched;

  useEffect(() => {
    if (policyProp !== undefined) return; // 부모가 데이터를 소유
    setFetched(null);
    setFailed(false);
    getEffectivePolicyAction(userId).then((r) => {
      if (r.success) setFetched(r.data);
      else setFailed(true);
    });
  }, [userId, policyProp]);

  if (failed) {
    return <p className="text-xs text-destructive py-1">{t('loadFailed')}</p>;
  }
  if (!policy) {
    return <p className="text-xs text-muted-foreground py-1">{t('loading')}</p>;
  }

  const modelAliases = [...new Set(policy.cells.map((c) => c.model_alias))].sort();
  // 컬럼 순서는 GATEWAY_CLIENTS 고정 — cells 의 발견 순서에 맡기면 행마다 축이 흔들린다.
  const present = new Set(policy.cells.map((c) => c.client));
  const clients = [
    ...GATEWAY_CLIENTS.filter((cl) => present.has(cl)),
    ...[...present].filter((cl) => !(GATEWAY_CLIENTS as readonly string[]).includes(cl)),
  ];

  const cellAt = (client: string, alias: string): EffectivePolicyCell | undefined =>
    policy.cells.find((c) => c.client === client && c.model_alias === alias);

  return (
    <div className="space-y-3">
      {/* 상: 다운그레이드 다이어그램(전체 폭 — 노드/엣지가 읽힐 크기가 필요) /
          하: 모델×앱 매트릭스. 규칙이 없어도 빈 상태를 명시한다 — 영역을 통째로
          없애면 "미설정" 인지 "로딩 실패" 인지 읽히지 않는다. */}
      <div className="space-y-4">
      <div>
        <p className="text-xs font-medium mb-1.5">{t('downgrade')}</p>
        {policy.downgrade_rules.length > 0 ? (
          <DowngradeDiagram
            rules={policy.downgrade_rules}
            models={models ?? []}
            formatOutPrice={(m) => {
              const per1m = m.output_price_per_1k * 1000;
              return `$${per1m.toFixed(2).replace(/\.?0+$/, '')}/1M output`;
            }}
            edgeTag={(r) => (r.scope === 'TEAM' ? t('scopeTeam') : t('scopeUser'))}
          />
        ) : (
          <p className="text-xs text-muted-foreground rounded-md border border-dashed border-border px-3 py-2">
            {t('downgradeNone')}
          </p>
        )}
      </div>

      {/* 모델 × 앱 매트릭스 — 행=모델, 열=앱. 웹서치는 모델과 무관한 앱별 값이라
          표 맨 아래 행으로 둔다.
          w-full 로 카드 폭을 채운다 — w-auto 시 표가 좌측에 붙어 나머지 공간이
          비어 보였다. 횡 스크롤 래퍼는 여전히 두지 않는다 — overflow-x-auto 는
          CSS 규칙상 overflow-y 도 auto 로 바꿔 ✗ 툴팁(위로 뜸)을 자른다. */}
      <div>
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b">
              <th className="text-left py-1 pr-4 font-medium text-muted-foreground">{t('model')}</th>
              {clients.map((client) => (
                <th key={client} className="text-center py-1 px-3 font-medium text-muted-foreground whitespace-nowrap">
                  {client}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {modelAliases.map((m) => (
              <tr key={m} className="border-b last:border-0">
                <td className="py-1.5 pr-2 font-medium whitespace-nowrap">{m}</td>
                {clients.map((client) => {
                  const cell = cellAt(client, m);
                  if (!cell) return <td key={client} className="text-center">-</td>;
                  if (cell.allowed) {
                    return (
                      <td key={client} className="text-center text-teal-600" aria-label={t('allowed')}>✓</td>
                    );
                  }
                  const reasons = cell.blocked_by
                    .filter((a): a is (typeof AXIS_KEYS)[number] =>
                      (AXIS_KEYS as readonly string[]).includes(a),
                    )
                    .map((a) => t(`axis.${a}`));
                  return (
                    <td key={client} className="text-center">
                      {/* title 어트리뷰트 툴팁은 표시 지연·무시되는 환경이 있어
                          CSS 팝오버로 대체 — hover 와 키보드 focus 둘 다 동작한다. */}
                      <span className="relative inline-flex group">
                        <button
                          type="button"
                          className="text-destructive rounded-sm px-0.5 leading-none focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                          aria-label={`${t('denied')}: ${reasons.join(', ')}`}
                        >
                          ✗
                        </button>
                        <span
                          role="tooltip"
                          className="pointer-events-none invisible absolute bottom-full left-1/2 z-50 mb-1.5 w-max max-w-56 -translate-x-1/2 rounded-md border border-border bg-popover px-2.5 py-2 text-left text-xs text-popover-foreground shadow-md opacity-0 transition-opacity duration-100 group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"
                        >
                          <span className="block font-medium mb-1">{t('deniedTitle')}</span>
                          <ul className="list-disc pl-3.5 space-y-0.5">
                            {reasons.map((r) => (
                              <li key={r}>{r}</li>
                            ))}
                          </ul>
                        </span>
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
            {/* 웹서치 — 앱별 토글이라 모델 행들과 같은 표의 마지막 행으로 표현 */}
            <tr className="border-b last:border-0">
              <td className="py-1.5 pr-2 font-medium whitespace-nowrap text-muted-foreground">
                {t('webSearch')}
              </td>
              {clients.map((client) => (
                <td key={client} className="text-center">
                  {policy.web_search[client] ? (
                    <span className="text-teal-600">✓</span>
                  ) : (
                    <span className="text-muted-foreground">-</span>
                  )}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
      </div>

      {/* 정책 출처 + rate limit — 하나의 컨테이너로 묶어 "이 사용자에게 적용된
          스코프 요약" 임을 시각적으로 구분한다.
          예산은 바로 위의 예산 섹션(BudgetGaugeRow)이 이미 같은 데이터를
          보여주므로 여기서는 생략한다 — 카드는 접근 판정에 집중. */}
      <div className="rounded-lg border border-border/60 bg-muted/30 px-3 py-2.5 space-y-1.5">
        <div className="flex items-baseline justify-between gap-3 text-xs">
          <span className="text-muted-foreground">{t('modelsSource.label')}</span>
          <span className="font-medium text-foreground text-right">
            {policy.allowed_models_source === 'none'
              ? t('modelsSource.none')
              : t(`modelsSource.${policy.allowed_models_source}`)}
          </span>
        </div>

        {/* 앱 정책 출처 — user > team > organization 폴백 중 실제로 적용된 스코프 */}
        <div className="flex items-baseline justify-between gap-3 text-xs">
          <span className="text-muted-foreground">{t('clientsSource.label')}</span>
          <span className="font-medium text-foreground text-right">
            {policy.allowed_clients_source === 'none'
              ? t('clientsSource.none')
              : t(`clientsSource.${policy.allowed_clients_source}`)}
          </span>
        </div>

        {/* rate limit 요약 */}
        <div className="border-t border-border/50 pt-1.5">
          <p className="text-xs font-medium mb-1">{t('rateLimits')}</p>
          {policy.rate_limits.length > 0 ? (
            <ul className="text-xs text-muted-foreground space-y-0.5">
              {policy.rate_limits.map((r, i) => (
                <li key={i}>
                  {r.scope}
                  {r.model_alias ? ` · ${r.model_alias}` : ''} —{' '}
                  {[
                    r.rpm_limit != null && `RPM ${r.rpm_limit}`,
                    r.tpm_limit != null && `TPM ${r.tpm_limit}`,
                    r.cpm_limit_usd != null && `$${r.cpm_limit_usd}/min`,
                    r.cph_limit_usd != null && `$${r.cph_limit_usd}/hr`,
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted-foreground">—</p>
          )}
        </div>
      </div>

    </div>
  );
}
