// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

/**
 * New-password route — completes Cognito's NEW_PASSWORD_REQUIRED challenge.
 *
 * `LoginForm` calls this after `/api/auth/login` returned a challenge. Forwards
 * to admin-api `POST /v1/auth/admin/new-password`, which calls Cognito
 * RespondToAuthChallenge and finishes the same OIDC verify → role/team mapping →
 * self-signed JWT flow as a normal login. Sets the `admin_jwt` cookie on success.
 */

import { NextRequest, NextResponse } from 'next/server';
import {
  ADMIN_API_URL,
  clientIpHeader,
  withAdminSessionCookie,
  type AdminErrorBody,
  type AdminLoginSuccess,
} from '@/lib/adminSessionCookie';

export async function POST(request: NextRequest): Promise<NextResponse> {
  let email: string | undefined;
  let newPassword: string | undefined;
  let cognitoSession: string | undefined;

  try {
    const body = (await request.json()) as {
      email?: string;
      new_password?: string;
      cognito_session?: string;
    };
    email = body.email;
    newPassword = body.new_password;
    cognitoSession = body.cognito_session;
  } catch {
    return NextResponse.json({ error: 'Invalid request body' }, { status: 400 });
  }

  if (!email || !newPassword || !cognitoSession) {
    return NextResponse.json(
      { error: 'email, new_password and cognito_session are required' },
      { status: 400 }
    );
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${ADMIN_API_URL}/v1/auth/admin/new-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...clientIpHeader(request) },
      body: JSON.stringify({
        email,
        new_password: newPassword,
        cognito_session: cognitoSession,
      }),
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
      { error: errorBody.error?.message ?? 'Could not set new password' },
      { status: upstream.status }
    );
  }

  const result = (await upstream.json()) as AdminLoginSuccess;
  return withAdminSessionCookie(request, result);
}
