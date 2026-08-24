// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

/**
 * Shared helper for the two Cognito login routes (`/api/auth/login` and
 * `/api/auth/new-password`) — both end by setting the same `admin_jwt` cookie
 * from an admin-api login response.
 */

import { NextRequest, NextResponse } from 'next/server';

export const ADMIN_API_URL = process.env.ADMIN_API_URL || 'http://admin-api:8080';

export interface AdminLoginSuccess {
  token: string;
  expires_at: number; // epoch seconds
  role: string;
  email: string;
  display_name: string;
  team_id: string | null;
}

export interface AdminChallenge {
  challenge: string; // e.g. "NEW_PASSWORD_REQUIRED"
  cognito_session: string;
}

export interface AdminErrorBody {
  error?: { type?: string; message?: string };
}

export function isAdminChallenge(body: AdminLoginSuccess | AdminChallenge): body is AdminChallenge {
  return 'challenge' in body;
}

/**
 * 브라우저의 실제 IP를 뽑아 admin-api 로 전달할 헤더 값을 만든다.
 *
 * 로그인 라우트는 admin-ui(Next.js 서버)가 admin-api 를 서버 간 호출로 부르므로,
 * admin-api 입장에서 `request.client.host` 는 실제 사용자가 아니라 admin-ui pod
 * 자체의 IP다 — 이 값으로 로그인 실패 횟수를 제한하면 모든 사용자의 시도가 한
 * 버킷에 합쳐져 한 사람의 실수로 전체가 잠기는 문제가 생긴다. ALB → admin-ui 홉의
 * `x-forwarded-for`(진짜 클라이언트가 맨 앞)를 그대로 admin-api 에 넘겨준다.
 */
export function clientIpHeader(request: NextRequest): Record<string, string> {
  const xff = request.headers.get('x-forwarded-for');
  const ip = xff?.split(',')[0]?.trim();
  return ip ? { 'X-Forwarded-For': ip } : {};
}

/** Sets the `admin_jwt` cookie from a successful admin-api login response. */
export function withAdminSessionCookie(
  request: NextRequest,
  result: AdminLoginSuccess
): NextResponse {
  const proto = request.headers.get('x-forwarded-proto') || 'http';
  const maxAge = Math.max(1, result.expires_at - Math.floor(Date.now() / 1000));

  const response = NextResponse.json({ ok: true });
  response.cookies.set('admin_jwt', result.token, {
    httpOnly: true,
    sameSite: 'lax',
    path: '/',
    maxAge,
    // secure: true 로 하면 HTTP 환경에선 브라우저가 쿠키를 저장하지 못해 무한 리다이렉트.
    secure: proto === 'https',
  });
  return response;
}
