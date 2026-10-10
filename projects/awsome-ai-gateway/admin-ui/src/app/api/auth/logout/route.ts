// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

/**
 * Logout route — clears the admin_jwt cookie and redirects to the login entry.
 *
 * Mirrors the proto/host handling in dev-login/route.ts so the Set-Cookie
 * `secure` flag matches the actual connection scheme (HTTP vs HTTPS) and
 * the redirect URL preserves the original Host header (avoids 0.0.0.0 in
 * containerized envs).
 *
 * OIDC(hosted-UI) 배포에서는 로컬 쿠키만 지우는 것으로 끝나지 않는다 — IdP 쪽
 * 세션 쿠키가 살아 있어 '/' → '/api/auth/login' 재진입이 조용히 재인증으로
 * 이어지고, 사용자는 "로그아웃이 안 된다" 를 보게 된다. 그래서 OIDC 배포에서는
 * IdP 의 logout 엔드포인트로 보내 IdP 세션까지 끊는다. logout_uri 는 앱
 * 클라이언트의 Allowed sign-out URLs 에 등록된 값이어야 한다 — 미등록이면
 * Cognito 가 400 을 뱉는다(운영 문서 docs/us-llm-gateway/ops/8-L-admin-login.md
 * 의 로그아웃 항목 참조).
 */

import { NextRequest, NextResponse } from 'next/server';
import { redirectRelative } from '@/lib/redirect';

/**
 * IdP logout 엔드포인트. OIDC_LOGOUT_URL 이 있으면 그대로 쓴다.
 * "off"(대소문자 무관)면 IdP 로그아웃을 완전히 끄고 로컬 쿠키만 지운다 —
 * sign-out URL 이 아직 Cognito 에 등록되지 않은 배포 직전·중간 상태에서
 * 로그아웃이 400 으로 막히는 걸 피하는 비상 스위치다.
 * 없으면 authorize URL 오리진의 /logout 을 유도하되, 그 규약({hosted-domain}/logout)
 * 은 Cognito 에만 맞으므로 amazoncognito.com 호스트일 때만 유도한다 — 다른 IdP 는
 * OIDC_LOGOUT_URL 을 명시해야 동작한다.
 */
function oidcLogoutEndpoint(): string {
  const explicit = (process.env.OIDC_LOGOUT_URL ?? '').trim();
  if (explicit.toLowerCase() === 'off') return '';
  if (explicit) return explicit;
  const authorize = (process.env.OIDC_AUTHORIZE_URL ?? '').trim();
  if (!authorize) return '';
  try {
    const url = new URL(authorize);
    if (!url.hostname.endsWith('amazoncognito.com')) return '';
    return `${url.origin}/logout`;
  } catch {
    return '';
  }
}

/**
 * Cognito `logout_uri` — Allowed sign-out URLs 에 등록된 오리진이어야 한다.
 * Host 헤더는 CloudFront/ALB 를 거치며 내부 이름(ALB DNS)이 될 수 있어 그대로
 * 쓰면 미등록 오리진이라 Cognito 가 400 을 뱉는다. OIDC_REDIRECT_URI 는
 * 19-admin-login.sh 가 등록한 공개 오리진의 콜백이므로 그 오리진을 우선 쓰고,
 * 없을 때(로컬/사전 구성)만 Host 로 떨어진다.
 */
function logoutUri(proto: string, host: string): string {
  const redirect = (process.env.OIDC_REDIRECT_URI ?? '').trim();
  if (redirect) {
    try {
      return `${new URL(redirect).origin}/`;
    } catch {
      // fall through to the Host-derived origin
    }
  }
  return `${proto}://${host}/`;
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  // proto 는 쿠키의 Secure 플래그 판정과 IdP logout 의 logout_uri 에 쓴다(HTTP
  // 종단에서 Secure 를 붙이면 브라우저가 쿠키를 저장하지 못한다). 프록시가 헤더를
  // 체인으로 붙일 수 있으므로 middleware 와 같은 규칙으로 첫 토큰만 쓴다.
  const proto =
    (request.headers.get('x-forwarded-proto') || 'http').split(',')[0].trim() || 'http';

  const clientId = (process.env.OIDC_CLIENT_ID ?? '').trim();
  const logoutEndpoint = clientId ? oidcLogoutEndpoint() : '';

  let response: NextResponse;
  if (logoutEndpoint) {
    const host = request.headers.get('host') || 'localhost:3000';
    // 같은 오리진이 아니라 IdP 로 나가는 리다이렉트 — 절대 URL 이 맞다
    // (redirect.test.ts 가 이 한 곳을 예외로 허용한다).
    const url = new URL(logoutEndpoint);
    url.searchParams.set('client_id', clientId);
    url.searchParams.set('logout_uri', logoutUri(proto, host));
    response = NextResponse.redirect(url.toString(), { status: 303 });
  } else {
    // 상대 Location — Host 헤더가 CloudFront 뒤에서 ALB 이름일 수 있다(lib/redirect.ts).
    // OIDC 미구성이면 '/' 로 보내 middleware 가 '/api/auth/login' 으로 갈라준다.
    response = redirectRelative('/');
  }
  response.cookies.set('admin_jwt', '', {
    httpOnly: true,
    sameSite: 'lax',
    path: '/',
    maxAge: 0,
    secure: proto === 'https',
  });
  return response;
}
