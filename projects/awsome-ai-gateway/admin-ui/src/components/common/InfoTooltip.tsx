'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

import { Info } from 'lucide-react';

/**
 * ? / ⓘ 아이콘 + CSS 팝오버 — 테이블 헤더 등에서 용어를 즉석 설명한다.
 * title 어트리뷰트는 표시가 지연되고 키보드 포커스에서 열리지 않으므로
 * group-hover/group-focus-within 팝오버를 쓴다.
 */
export function InfoTooltip({
  label,
  children,
  align = 'left',
  side = 'bottom',
}: {
  label: string;
  children: React.ReactNode;
  /** 좌측 끝 컬럼 등에서 중앙 정렬이면 사이드바/뷰포트 밖으로 넘친다 — 기본은 좌측 앵커. */
  align?: 'left' | 'center';
  /** 아이콘 바로 아래에 폼 컨트롤이 오면 팝오버가 그 위를 덮어 읽히지 않는다 —
      그런 배치에서는 'top' 으로 위쪽에 연다. */
  side?: 'top' | 'bottom';
}) {
  const vertical =
    side === 'top' ? 'bottom-full mb-1.5' : 'top-full mt-1.5';
  return (
    <span className="relative inline-flex group align-middle">
      <button
        type="button"
        aria-label={label}
        className="text-muted-foreground/70 hover:text-muted-foreground rounded-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      >
        <Info size={12} aria-hidden="true" />
      </button>
      <span
        role="tooltip"
        className={`pointer-events-none invisible absolute z-50 w-64 whitespace-normal rounded-md border border-border bg-popover px-2.5 py-2 text-left text-xs font-normal leading-relaxed normal-case tracking-normal text-popover-foreground shadow-md opacity-0 transition-opacity duration-100 group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100 ${vertical} ${
          align === 'center' ? 'left-1/2 -translate-x-1/2' : 'left-0'
        }`}
      >
        {children}
      </span>
    </span>
  );
}
