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
 * 세션 쿠키가 살아 있어 /login → / → /api/auth/login 재진입이 조용히 재인증으로
 * 이어지고, 사용자는 "로그아웃이 안 된다" 를 보게 된다. 그래서 OIDC 배포에서는
 * IdP 의 logout 엔드포인트(Cognito 는 {hosted-domain}/logout)로 보내 IdP 세션까지
 * 끊는다. logout_uri 는 앱 클라이언트의 Allowed sign-out URLs 에 등록된 값이어야
 * 한다 — 미등록이면 Cognito 가 400 을 뱉는다.
 */

import { NextRequest, NextResponse } from 'next/server';
import { redirectRelative } from '@/lib/redirect';

/** OIDC_LOGOUT_URL → 없으면 authorize URL 의 오리진에서 유도 (Cognito 는 /logout). */
function oidcLogoutEndpoint(): string {
  const explicit = (process.env.OIDC_LOGOUT_URL ?? '').trim();
  if (explicit) return explicit;
  const authorize = (process.env.OIDC_AUTHORIZE_URL ?? '').trim();
  if (!authorize) return '';
  try {
    return `${new URL(authorize).origin}/logout`;
  } catch {
    return '';
  }
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  // proto 는 쿠키의 Secure 플래그 판정에 쓴다(HTTP 종단에서 Secure 를 붙이면
  // 브라우저가 쿠키를 저장하지 못한다). host 는 IdP logout 의 logout_uri 를 만든다.
  const proto = request.headers.get('x-forwarded-proto') || 'http';

  const clientId = (process.env.OIDC_CLIENT_ID ?? '').trim();
  const logoutEndpoint = clientId ? oidcLogoutEndpoint() : '';

  let response: NextResponse;
  if (logoutEndpoint) {
    const host = request.headers.get('host') || 'localhost:3000';
    // 같은 오리진이 아니라 IdP 로 나가는 리다이렉트 — 절대 URL 이 맞다
    // (redirect.test.ts 가 이 한 곳을 예외로 허용한다).
    const url = new URL(logoutEndpoint);
    url.searchParams.set('client_id', clientId);
    url.searchParams.set('logout_uri', `${proto}://${host}/`);
    response = NextResponse.redirect(url.toString(), { status: 303 });
  } else {
    // 상대 Location — Host 헤더가 CloudFront 뒤에서 ALB 이름일 수 있다(lib/redirect.ts).
    response = redirectRelative('/login');
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
