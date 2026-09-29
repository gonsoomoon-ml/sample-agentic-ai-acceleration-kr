// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

/**
 * 401(인증 만료·무효) 공용 처리 — 프록시 라우트(server)와 클라이언트 컴포넌트가 같이 쓴다.
 *
 * ⚠️ 배경: admin-ui 에는 **실행되는 401 분기가 하나도 없었다**. api-client 는 상태코드를
 *    구분하지 않고 전부 APIError 로 던졌고, `grep -rn "401" src/` 는 주석만 잡혔다.
 *    middleware 는 이걸 못 막는다 — src/middleware.ts 의 공개 경로 분기가 `/api/` 전체를
 *    통과시키므로, 브라우저가 직접 때리는 프록시 라우트(src/app/api/**)에는 만료 검사가
 *    아예 걸리지 않는다.
 *
 *    재현: 대시보드를 쿠키 TTL 이 지날 때까지 열어두고 팀 필터만 바꾼다. 문서
 *    내비게이션이 없으니 middleware 는 돌지 않고, fetch 는 /api/dashboard/* 로 가고,
 *    admin-api 가 401 을 주고, 카드에는 "조회 실패: HTTP 401" 이 찍히면서 **낡은 숫자가
 *    화면에 그대로 남는다**. 만료가 아닌 401(admin_jwt_configs.is_active 토글, 서명키
 *    회전)은 middleware 가 원리적으로 탐지조차 못 한다 — 401 은 응답을 보고 처리해야 한다.
 *
 * ⚠️ 403 은 절대 이 경로에 넣지 않는다. TEAM_LEADER 가 admin 전용 엔드포인트에서 받는
 *    403 은 정상 상태이고, 화면은 '—' 로 렌더해야 한다(src/app/page.tsx 의 allSettled
 *    처리 참조). 403 을 로그인으로 튕기면 권한이 낮은 사용자가 대시보드를 아예 못 쓴다.
 *    그래서 판정은 `=== 401` 엄격 비교이며, `!res.ok` 로 뭉개지 않는다.
 */

/**
 * 로그인 진입점. 쿼리(?next=…)를 붙이지 않는 이유: middleware 의 로그인 리다이렉트도
 * search 를 비워서 보내므로 진입점 계약을 하나로 유지한다.
 */
export const LOGIN_PATH = '/login';

/** 프록시 라우트 401 본문의 기계 판독용 코드. */
export const UNAUTHORIZED_ERROR_CODE = 'UNAUTHORIZED';

/**
 * 사람이 읽는 부분(로그·개발자 도구). 화면에 찍히는 문자열이 아니다 —
 * 클라이언트는 이 응답을 받으면 문구를 렌더하지 않고 로그인으로 이동한다.
 */
export const UNAUTHORIZED_MESSAGE = '세션이 만료되었거나 인증이 유효하지 않습니다';

/**
 * 프록시 라우트가 내려보내는 401 본문. 상태코드만으로도 판정 가능하지만, 본문에
 * error_code 를 같이 넣어야 상태코드를 무시하는 기존 소비자(예:
 * src/lib/utils/rateLimitUsage.ts 는 res.ok 를 보지 않고 본문만 읽는다)도 구분할 수 있다.
 */
export function unauthorizedBody(): {
  error_code: string;
  error: string;
  login_url: string;
} {
  return {
    error_code: UNAUTHORIZED_ERROR_CODE,
    error: UNAUTHORIZED_MESSAGE,
    login_url: LOGIN_PATH,
  };
}

/**
 * 401 이면 로그인으로 이동시키고 true 를 돌려준다(호출부는 곧바로 return 해야 한다).
 *
 * 반환값의 의미는 "이 응답은 401 이었다" 이지 "이동에 성공했다" 가 아니다 —
 * window 가 없는 실행 컨텍스트(서버 렌더/테스트)에서도 호출부가 정상 경로를
 * 계속 타지 않도록 판정 자체는 그대로 돌려준다.
 */
export function redirectToLoginIfUnauthorized(
  res: { status: number } | null | undefined,
): boolean {
  if (res?.status !== 401) return false;
  if (typeof window !== 'undefined') {
    // replace 가 아니라 assign: 뒤로가기로 돌아왔을 때 middleware 가 다시 판정하게 둔다.
    window.location.assign(LOGIN_PATH);
  }
  return true;
}
