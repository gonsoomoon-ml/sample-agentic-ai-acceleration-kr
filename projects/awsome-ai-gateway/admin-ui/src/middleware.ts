// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

/**
 * Next.js Middleware — SEC-01 pattern.
 *
 * Applies security headers to all responses and enforces JWT-based
 * authentication for non-API page routes.
 */

import { NextRequest, NextResponse } from 'next/server';
import { parseJWT } from '@/lib/auth';
import { checkPagePermission, isSessionExpired } from '@/lib/auth';

export const config = {
  // icon.svg 는 app/icon.svg 에서 나오는 파비콘 — 제외하지 않으면 매 페이지 로드마다
  // checkPagePermission('/icon.svg') 가 default-deny 에 걸려 307 → /403 이 되고,
  // 브라우저는 이미지 요청으로 /403 HTML 을 받는다(네트워크 탭의 유령 403 의 정체).
  matcher: [
    '/((?!_next/static|_next/image|favicon\\.ico|icon\\.svg|apple-icon\\.(?:png|svg)|robots\\.txt|sitemap\\.xml|manifest\\.(?:json|webmanifest)).*)',
  ],
};

const SECURITY_HEADERS: Record<string, string> = {
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
  'Content-Security-Policy':
    "default-src 'self'; script-src 'self' 'unsafe-eval' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self' data:",
};

function applySecurityHeaders(response: NextResponse): NextResponse {
  for (const [key, value] of Object.entries(SECURITY_HEADERS)) {
    response.headers.set(key, value);
  }
  return response;
}

/**
 * 로그인 화면으로 되돌린다. `clearCookie` 면 남아 있는 admin_jwt 도 지운다.
 *
 * nextUrl 을 쓰는 이유: request.url 은 Docker 안에서 0.0.0.0 으로 풀려 원래 호스트를 잃는다.
 *
 * ⚠️ 목적지가 `/api/auth/dev-login` 이 아니라 `/login` 인 이유(A3):
 *    dev-login 라우트는 DEV_LOGIN_ENABLED !== 'true' 이면 본문 없는 404 를 준다. prod 는
 *    그 값이 "false" 이므로 예전 목적지로는 **prod 브라우저가 307 → 빈 404 에서 끝났다** —
 *    ALB 인증도 없어 로그인 경로가 0개였다.
 *    `/login` 은 8-L 의 로그인 페이지다 — Cognito 이메일/비밀번호 폼이 상시 렌더되고,
 *    DEV_LOGIN_ENABLED=true 일 때만 "Sign in with dev mode (dev-login)" 링크가 붙는다
 *    (app/login/page.tsx → components/auth/LoginForm.tsx). `/api/auth/login` GET 은
 *    OIDC env 가 비어 있고 dev-login 이 켜진 배포에서 dev 폼으로 307 하는데, 그러면
 *    로그인 화면이 /login 이 아니라 dev 폼으로 새어 사용자에게 두 화면이 보인다.
 *    `/login` 은 아래 public 예외에 걸려 미인증으로도 도달 가능하다 — 그래야 무한
 *    리다이렉트가 안 난다.
 */
/**
 * 미들웨어의 리다이렉트는 **절대 URL** 이어야 한다. Next.js 14.2 의 미들웨어 어댑터가
 * 응답의 Location 을 `new NextURL(location)` 으로 다시 해석하는데(next/dist/server/web/
 * adapter.js) 상대 경로에는 base 가 없어 `TypeError: Invalid URL` 이 나고, 미인증·만료
 * 요청이 오는 **모든 페이지가 500** 이 된다(2026-09-15 US dev 실측). 라우트 핸들러
 * (app/api/*)의 redirectRelative 는 그 어댑터를 타지 않으므로 그대로 둔다.
 *
 * 오리진은 request.url(컨테이너 안에서 0.0.0.0/localhost 로 풀린다)이 아니라 login·
 * callback·logout 라우트와 같은 헤더 규칙으로 만든다 — x-forwarded-proto 의 첫 토큰
 * + Host. CloudFront 뒤에서 Host 가 ALB 이름으로 오는 구성은 redirect_uri 도 같은 값을
 * 쓰므로 이미 viewer Host 전달이 전제다.
 */
function externalOrigin(request: NextRequest): string {
  const host = request.headers.get('host') || request.nextUrl.host || 'localhost:3000';
  const rawProto =
    request.headers.get('x-forwarded-proto') || request.nextUrl.protocol.replace(/:$/, '') || 'http';
  const proto = rawProto.split(',')[0].trim() || 'http';
  return `${proto}://${host}`;
}

/** 같은 오리진 안의 경로로만 보낸다 — `//evil.com`·`/\evil.com` 은 스킴 상대 URL 이라 거부. */
function redirectSameOrigin(request: NextRequest, path: string, status: number): NextResponse {
  if (!path.startsWith('/') || path.startsWith('//') || path.startsWith('/\\')) {
    throw new Error(`redirectSameOrigin: same-origin path expected, got ${path}`);
  }
  return NextResponse.redirect(new URL(path, externalOrigin(request)), status);
}

/** OIDC(hosted-UI SSO) 배포인가 — adminUi.env 의 OIDC_CLIENT_ID 유무가 스위치. */
function hostedUiOidc(): boolean {
  return (process.env.OIDC_CLIENT_ID ?? '').trim() !== '';
}

function redirectToLogin(request: NextRequest, clearCookie: boolean): NextResponse {
  // 로그인 방식은 배포별로 둘 중 하나다. adminUi.env 의 OIDC_* 가 채워져 있으면
  // hosted-UI SSO(Authorization Code+PKCE) 배포 — /api/auth/login GET 이 IdP 로 302.
  // 그게 아니면 /login 페이지(8-L Cognito 폼; DEV_LOGIN_ENABLED 면 dev-login 링크도 표시).
  // OIDC 배포에서 /login 으로내면 ROPC 가 꺼져 있어 폼이 동작하지 않는다.
  const redirectResponse = redirectSameOrigin(
    request,
    hostedUiOidc() ? '/api/auth/login' : '/login',
    307,
  );
  if (clearCookie) {
    // 만료/손상된 자격증명은 응답에서 즉시 제거한다 — 안 지우면 다음 요청도 같은 쿠키로
    // 다시 이 분기를 타고, 사용자는 못 쓰는 쿠키를 계속 들고 다닌다.
    redirectResponse.cookies.delete('admin_jwt');
  }
  applySecurityHeaders(redirectResponse);
  return redirectResponse;
}

export async function middleware(request: NextRequest): Promise<NextResponse> {
  const { pathname } = request.nextUrl;

  // Always start with a pass-through response so we can attach headers
  const response = NextResponse.next();
  applySecurityHeaders(response);

  // Public routes — no auth required.
  // '/403' must be public, otherwise an authenticated user without permission
  // for the current path gets redirected to /403, which itself fails the
  // permission check, and bounces back to /403 → ERR_TOO_MANY_REDIRECTS.
  if (
    pathname.startsWith('/api/') ||
    pathname.startsWith('/cli') ||
    pathname === '/login' ||
    pathname === '/403'
  ) {
    // OIDC 배포에서는 ROPC 폼 경로(/login)를 닫는다 — 비밀번호가 우리 서버를 통과하는
    // 경로는 HTTPS IdP 로그인이 가능해진 시점에 제거하는 게 맞다. 쿠키가 있든 없든
    // '/' 로내면 세션이 살아 있으면 대시보드, 아니면 단일 진입점(/api/auth/login)으로
    // 다시 분기된다.
    if (pathname === '/login' && hostedUiOidc()) {
      const res = redirectSameOrigin(request, '/', 307);
      applySecurityHeaders(res);
      return res;
    }
    // '/login' 은 로그아웃 상태의 페이지다. 클라이언트 측 401 핸들러가 쿠키를 지우지
    // 않고 assign('/login') 하므로(만료 외에 admin-api 가 거절하는 유효 형태의 JWT —
    // 서명키 교체·admin_jwt_configs 토글 등), 쿠키가 남은 채 /login 에 오면 layout 이
    // 그걸 세션으로 해석해 사이드바+헤더 위에 로그인 폼이 겹쳐 그려진다. 쿠키를 들고
    // 온 /login 요청은 지우고 한 번 더 /login 으로 보낸다 — 다음 요청은 쿠키 없이
    // 도착해 깨끗한 전체화면 로그인이 렌더된다.
    if (pathname === '/login' && request.cookies.get('admin_jwt')?.value) {
      return redirectToLogin(request, true);
    }
    return response;
  }

  const jwtCookie = request.cookies.get('admin_jwt');

  // No JWT present — redirect through the single login entry point.
  if (!jwtCookie?.value) {
    return redirectToLogin(request, false);
  }

  // JWT present — parse, check expiry, then check permissions
  try {
    const session = parseJWT(jwtCookie.value);

    // ⚠️ 만료 검사. 예전엔 이 분기가 아예 없어서 만료된 admin_jwt 로도 페이지가 열렸고,
    //    서버 컴포넌트의 모든 admin-api 호출만 401 이 됐다 — 화면 전체가 '—' 와
    //    fetchFailed 로 덮이고 로그인으로 돌아갈 방법이 없었다. 만료는 미인증과 같게
    //    취급한다(쿠키 제거 + 로그인 리다이렉트).
    //    isSessionExpired 는 exp 클레임이 없거나 해석 불가면 false 를 준다 — dev 토큰에는
    //    숫자 exp 가 없어서, 여기서 만료로 몰면 dev 로그인이 무한 리다이렉트가 된다.
    if (isSessionExpired(session)) {
      return redirectToLogin(request, true);
    }

    const hasPermission = checkPagePermission(pathname, session.role);

    if (!hasPermission) {
      const redirectResponse = redirectSameOrigin(request, '/403', 307);
      applySecurityHeaders(redirectResponse);
      return redirectResponse;
    }
  } catch {
    // Malformed JWT — treat as unauthenticated
    return redirectToLogin(request, true);
  }

  return response;
}
