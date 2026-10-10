// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

/**
 * locale 결정의 단일 규칙 — i18n/request.ts 와 app/layout.tsx 가 함께 쓴다.
 * 이 규칙이 두 곳에 따로 있으면 한쪽만 고쳐져 `lang="fr"` + ko 메시지 같은
 * 조합이 생긴다(실제로 layout.tsx 가 원시 쿠키값을 쓰던 사례).
 */
export const SUPPORTED_LOCALES = ['ko', 'en'] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];
export const DEFAULT_LOCALE: Locale = 'ko';

/** 쿠키 원시값을 지원 locale 로 정규화 — 목록에 없으면 DEFAULT_LOCALE. */
export function resolveLocale(raw: string | undefined | null): Locale {
  return raw && SUPPORTED_LOCALES.includes(raw as Locale) ? (raw as Locale) : DEFAULT_LOCALE;
}
