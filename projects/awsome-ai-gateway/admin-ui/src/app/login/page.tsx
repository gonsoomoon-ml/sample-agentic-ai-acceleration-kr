// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

import { redirect } from 'next/navigation';
import { cookies } from 'next/headers';
import { LoginForm } from '@/components/auth/LoginForm';
import { parseJWT } from '@/lib/auth';

export default function LoginPage() {
  // 이미 유효한 세션이 있으면 로그인 폼 대신 대시보드로.
  // redirect() 는 내부적으로 throw 하므로 try/catch 밖에서 호출해야 한다
  // (안에서 호출하면 우리 catch 가 그 특수 예외를 삼켜버림).
  const token = cookies().get('admin_jwt')?.value;
  let hasValidSession = false;
  if (token) {
    try {
      parseJWT(token);
      hasValidSession = true;
    } catch {
      // Malformed/expired token — fall through to show the login form.
    }
  }
  if (hasValidSession) {
    redirect('/');
  }

  return <LoginForm devLoginEnabled={process.env.DEV_LOGIN_ENABLED === 'true'} />;
}
