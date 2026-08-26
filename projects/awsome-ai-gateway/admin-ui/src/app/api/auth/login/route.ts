// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

/**
 * Login route — real Cognito authentication (replaces dev-login for normal use).
 *
 * admin-ui 커스텀 로그인 폼(POST email/password) → admin-api
 * `POST /v1/auth/admin/login` (Cognito InitiateAuth(USER_PASSWORD_AUTH) +
 * OIDC 검증 + 그룹→role/team 매핑) → 반환된 self-signed JWT 를 `admin_jwt`
 * 쿠키에 저장.
 *
 * 관리자가 콘솔/CLI 로 막 만든 계정(임시 비밀번호)이면 admin-api 가 토큰 대신
 * `{ challenge: "NEW_PASSWORD_REQUIRED", cognito_session }` 을 200 으로 반환한다 —
 * 이 라우트는 그걸 그대로 클라이언트에 전달하고(쿠키 없음), `LoginForm` 이 비밀번호
 * 설정 화면으로 전환해 `/api/auth/new-password` 로 이어서 로그인을 완료한다.
 *
 * admin-api 가 503 (admin_login_disabled) 을 반환하면 Cognito 연동이 아직
 * 설정되지 않은 것 — 로컬 개발 환경에서는 `/api/auth/dev-login` 을 계속 사용한다.
 */

import { NextRequest, NextResponse } from 'next/server';
import {
  ADMIN_API_URL,
  clientIpHeader,
  isAdminChallenge,
  withAdminSessionCookie,
  type AdminChallenge,
  type AdminErrorBody,
  type AdminLoginSuccess,
} from '@/lib/adminSessionCookie';

export async function POST(request: NextRequest): Promise<NextResponse> {
  let email: string | undefined;
  let password: string | undefined;

  try {
    const body = (await request.json()) as { email?: string; password?: string };
    email = body.email;
    password = body.password;
  } catch {
    return NextResponse.json({ error: 'Invalid request body' }, { status: 400 });
  }

  if (!email || !password) {
    return NextResponse.json({ error: 'email and password are required' }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${ADMIN_API_URL}/v1/auth/admin/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...clientIpHeader(request) },
      body: JSON.stringify({ email, password }),
      cache: 'no-store',
    });
  } catch {
    return NextResponse.json({ error: 'admin-api unreachable' }, { status: 502 });
  }

  if (!upstream.ok) {
    let errorBody: AdminErrorBody = {};
    try {
      errorBody = (await upstream.json()) as AdminErrorBody;
    } catch {
      // ignore — non-JSON error body
    }
    return NextResponse.json(
      { error: errorBody.error?.message ?? 'Login failed' },
      { status: upstream.status }
    );
  }

  const result = (await upstream.json()) as AdminLoginSuccess | AdminChallenge;

  if (isAdminChallenge(result)) {
    return NextResponse.json(result);
  }

  return withAdminSessionCookie(request, result);
}
