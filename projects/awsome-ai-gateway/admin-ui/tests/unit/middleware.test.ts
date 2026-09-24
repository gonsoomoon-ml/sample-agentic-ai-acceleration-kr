// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

/**
 * middleware — 만료 분기가 실제로 배선돼 있는지(순수 함수 테스트만으로는 증명 안 됨).
 *
 * 회귀: 만료된 admin_jwt 는 "쿠키가 있다"는 이유로 미인증 분기를 타지 않아 페이지가
 * 그대로 열렸고, 서버 컴포넌트의 admin-api 호출만 전부 401 이 됐다. 사용자에게는
 * 로그인으로 돌아갈 길이 없었다.
 */

import { describe, it, expect } from 'vitest';
import { NextRequest } from 'next/server';
import { middleware } from '@/middleware';

function b64url(obj: unknown): string {
  return Buffer.from(JSON.stringify(obj)).toString('base64url');
}

function tokenWithExp(offsetSeconds: number): string {
  return `header.${b64url({
    sub: '11111111-1111-1111-1111-111111111111',
    role: 'ADMIN',
    exp: Math.floor(Date.now() / 1000) + offsetSeconds,
  })}.sig`;
}

const DEV_TOKEN = `dev.${b64url({
  user_id: 'dev-admin',
  email: 'admin@dev.local',
  display_name: 'Dev Admin',
  role: 'ADMIN',
  team_id: null,
  department_id: null,
  issued_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 86_400_000).toISOString(),
})}.sig`;

function requestWith(cookie?: string, pathname = '/'): NextRequest {
  const req = new NextRequest(`http://admin.test${pathname}`);
  if (cookie !== undefined) {
    req.cookies.set('admin_jwt', cookie);
  }
  return req;
}

// ⚠️ 리다이렉트 목적지는 `/api/auth/dev-login` 이 아니라 `/login` 이다.
//    prod 는 `DEV_LOGIN_ENABLED=false` 라 dev-login GET 이 404 였고, admin_jwt 를 굽는
//    코드가 레포에 dev-login 뿐이어서 **prod 에는 로그인 경로가 아예 없었다**(미인증
//    브라우저 → 307 → 바디 없는 404 막다른 길). 이제 middleware 는 OIDC_* env 가
//    채워진 hosted-UI 배포에선 `/api/auth/login`(IdP 302 진입점), 그 외엔 8-L 의
//    `/login` 페이지(Cognito 폼 + DEV_LOGIN_ENABLED 면 dev-login 링크)로 보낸다
//    (src/middleware.ts 의 redirectToLogin). 이 파일은 OIDC_* 미설정 = `/login` 분기다.
//    으로 보낸다(src/app/api/auth/login/route.ts).
/**
 * 같은 오리진 리다이렉트의 계약: Location 은 **요청의 Host + x-forwarded-proto 로 만든
 * 절대 URL** 이고 경로만 다르다.
 *
 * 왜 상대 경로가 아닌가: Next.js 14.2 미들웨어 어댑터가 Location 을 `new NextURL()` 로
 * 다시 해석해 상대 경로면 `Invalid URL` 로 500 이 난다(라우트 핸들러는 무관). 왜
 * request.url 이 아닌가: 컨테이너 안에서 0.0.0.0 으로 풀린다. 그래서 login/callback
 * 라우트와 같은 헤더 규칙을 쓴다 — 테스트 요청의 Host 는 admin.test, proto 는 http.
 */
function expectSameOriginRedirect(res: Response, path: string): void {
  const loc = res.headers.get('location');
  expect(loc).not.toBeNull();
  const url = new URL(loc!);
  expect(url.host).toBe('admin.test');
  expect(url.protocol).toBe('http:');
  expect(url.pathname).toBe(path);
  // 절대 URL 이라도 `//host` 스킴 상대 형태로 새지 않는다.
  expect(loc!.startsWith('//')).toBe(false);
}

describe('middleware — session expiry', () => {
  it('redirects an EXPIRED token to login and clears the stale cookie', async () => {
    const res = await middleware(requestWith(tokenWithExp(-3600)));

    expect(res.status).toBe(307);
    expectSameOriginRedirect(res, '/login');
    // 못 쓰는 자격증명은 응답에서 제거돼야 한다.
    const setCookie = res.headers.get('set-cookie') ?? '';
    expect(setCookie).toContain('admin_jwt=');
    expect(setCookie).toMatch(/Max-Age=0|Expires=Thu, 01 Jan 1970/);
  });

  it('lets a LIVE token through', async () => {
    const res = await middleware(requestWith(tokenWithExp(3600)));
    expect(res.status).toBe(200);
    expect(res.headers.get('location')).toBeNull();
  });

  it('lets a dev-login token through — it carries no numeric exp', async () => {
    const res = await middleware(requestWith(DEV_TOKEN));
    expect(res.status).toBe(200);
    expect(res.headers.get('location')).toBeNull();
  });

  it('lets a token with NO expiry claim at all through (진짜 fail-open 분기)', async () => {
    // ⚠️ 위의 dev-login 테스트는 이 분기를 덮지 못한다. DEV_TOKEN 에는 ISO `expires_at` 이
    //    24시간 뒤로 들어 있어서 isSessionExpired 의 **ISO 파싱 분기**를 타고 false 가 된다.
    //    exp/expires_at 이 아예 없을 때의 관용(`if (!raw) return false`)은 그 테스트가
    //    통과해도 배선되지 않았을 수 있다 — 그러면 그런 토큰을 들고 온 사용자는 로그인
    //    직후 다시 로그인으로 튕겨 무한 리다이렉트에 빠진다. 그 분기를 직접 찍는다.
    const res = await middleware(requestWith(`dev.${b64url({ role: 'ADMIN' })}.sig`));
    expect(res.status).toBe(200);
    expect(res.headers.get('location')).toBeNull();
  });

  it('lets a token with an UNPARSEABLE expiry through — 판정 보류', async () => {
    // 만료를 확신할 수 없을 때 튕겨내면 정상 사용자를 잠글 수 있다.
    const res = await middleware(
      requestWith(`dev.${b64url({ role: 'ADMIN', expires_at: 'not-a-date' })}.sig`),
    );
    expect(res.status).toBe(200);
  });

  it('redirects when the expiry is inside the skew window (UI 가 먼저 만료로 본다)', async () => {
    // admin-api 의 jose leeway 는 0 이라(admin-api/src/app/core/auth.py:63) exp 시점에
    // 정확히 401 이 된다. UI 가 그보다 늦게 만료로 보면 그 구간에서 '페이지는 열리는데
    // 전부 401' 이라는 원래 증상이 재현된다. 10초 남은 토큰은 이미 로그인으로 보내야 한다.
    const res = await middleware(requestWith(tokenWithExp(10)));
    expect(res.status).toBe(307);
    expectSameOriginRedirect(res, '/login');
  });

  it('still redirects when no cookie is present (and does not clear anything)', async () => {
    const res = await middleware(requestWith(undefined));
    expect(res.status).toBe(307);
    expectSameOriginRedirect(res, '/login');
  });

  it('redirects a malformed token to login and clears the cookie', async () => {
    const res = await middleware(requestWith('not-a-jwt'));
    expect(res.status).toBe(307);
    expectSameOriginRedirect(res, '/login');
    expect(res.headers.get('set-cookie') ?? '').toContain('admin_jwt=');
  });

  it('applies security headers to the expiry redirect too', async () => {
    const res = await middleware(requestWith(tokenWithExp(-3600)));
    expect(res.headers.get('X-Frame-Options')).toBe('DENY');
    expect(res.headers.get('X-Content-Type-Options')).toBe('nosniff');
  });

  it('403 stays public — an expired cookie must not bounce it into a redirect loop', async () => {
    const res = await middleware(requestWith(tokenWithExp(-3600), '/403'));
    expect(res.status).toBe(200);
  });

  it('/login stays public — the login page must not redirect to itself', async () => {
    const res = await middleware(requestWith(undefined, '/login'));
    expect(res.status).toBe(200);
    expect(res.headers.get('location')).toBeNull();
  });

  it('/login WITH a stale cookie clears it and re-requests /login', async () => {
    // 클라이언트 측 401 핸들러는 쿠키를 지우지 않고 assign('/login') 한다 —
    // 쿠키가 남아 오면 layout 이 그걸 세션으로 해석해 셸 위에 로그인 폼이 겹친다.
    // 삭제 후 /login 으로 한 번 더내면 다음 요청은 쿠키 없이 도착한다.
    const res = await middleware(requestWith(tokenWithExp(-3600), '/login'));
    expect(res.status).toBe(307);
    expectSameOriginRedirect(res, '/login');
    const setCookie = res.headers.get('set-cookie') ?? '';
    expect(setCookie).toContain('admin_jwt=');
    expect(setCookie).toMatch(/Max-Age=0|Expires=Thu, 01 Jan 1970/);
  });

  it('/login clears even a LIVE-looking cookie — admin-api 401 경유 도착 포함', async () => {
    // 서명키 교체·admin_jwt_configs 토글처럼 "파싱·만료는 정상인데 admin-api 가
    // 거절하는" 쿠키로 /login 에 도달할 수 있다. 이 경우도 지워야 한다 —
    // 안 지우면 '/' ↔ /login 이 무한 루프다.
    const res = await middleware(requestWith(tokenWithExp(3600), '/login'));
    expect(res.status).toBe(307);
    expectSameOriginRedirect(res, '/login');
    expect(res.headers.get('set-cookie') ?? '').toContain('admin_jwt=');
  });
});

describe('middleware — hosted-UI OIDC 배포에서는 /api/auth/login 이다', () => {
  it('OIDC_CLIENT_ID 가 있으면 SSO 진입점으로 보낸다 (dead /login 폼 금지)', async () => {
    // adminUi.env 의 OIDC_* 가 채워진 배포는 /api/auth/login GET 이 IdP 로 302 하는
    // 유일한 진입점이다 — 그런 배포의 /login 은 ROPC 가 꺼져 있어 폼이 동작하지 않는다.
    const saved = process.env.OIDC_CLIENT_ID;
    process.env.OIDC_CLIENT_ID = 'admin-ui-client';
    try {
      const res = await middleware(requestWith(undefined));
      expect(res.status).toBe(307);
      expectSameOriginRedirect(res, '/api/auth/login');
    } finally {
      if (saved === undefined) delete process.env.OIDC_CLIENT_ID;
      else process.env.OIDC_CLIENT_ID = saved;
    }
  });

  it('OIDC 배포에서 /login 직접 접근은 / 로 낸다 (ROPC 폼 경로 폐쇄)', async () => {
    // 비밀번호가 우리 서버를 통과하는 ROPC 폼은 HTTPS IdP 로그인이 가능해진 배포에서
    // 제거 대상이다. / 로내면 세션이 살아 있으면 대시보드, 아니면 단일 진입점으로 간다.
    const saved = process.env.OIDC_CLIENT_ID;
    process.env.OIDC_CLIENT_ID = 'admin-ui-client';
    try {
      const res = await middleware(requestWith(undefined, '/login'));
      expect(res.status).toBe(307);
      expectSameOriginRedirect(res, '/');
    } finally {
      if (saved === undefined) delete process.env.OIDC_CLIENT_ID;
      else process.env.OIDC_CLIENT_ID = saved;
    }
  });
});
