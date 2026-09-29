// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

// 사용량 트렌드 프록시 — RateLimitConfigPanel 의 트렌드 차트가 노드/윈도우
// 변경 시 호출. admin-api /admin/rate-limits/usage-trend/{scope}/{scope_id} 로 전달.
// fail-soft 규칙은 usage 프록시와 동일(401 만 진짜 오류, 나머지는 available:false).

import { cookies } from 'next/headers';
import { NextRequest, NextResponse } from 'next/server';
import { unauthorizedBody } from '@/lib/utils/unauthorized';

const ADMIN_API_URL = process.env.ADMIN_API_URL || 'http://admin-api:8080';
const WINDOWS = new Set(['1h', '6h', '24h', '7d']);

export async function GET(req: NextRequest) {
  const jwt = cookies().get('admin_jwt')?.value;
  const sp = req.nextUrl.searchParams;
  const scope = sp.get('scope') ?? '';
  const scopeId = sp.get('scope_id') ?? '';
  const window = WINDOWS.has(sp.get('window') ?? '') ? sp.get('window')! : '24h';
  if (!scope || !scopeId) {
    return NextResponse.json({ available: false, reason: 'missing params' }, { status: 400 });
  }

  const upstream = `${ADMIN_API_URL}/admin/rate-limits/usage-trend/${encodeURIComponent(scope)}/${encodeURIComponent(scopeId)}?window=${window}`;
  try {
    const res = await fetch(upstream, {
      cache: 'no-store',
      headers: {
        'Content-Type': 'application/json',
        ...(jwt ? { Cookie: `admin_jwt=${jwt}` } : {}),
      },
    });
    if (res.status === 401) {
      return NextResponse.json(
        { ...unauthorizedBody(), available: false, reason: 'unauthorized' },
        { status: 401 },
      );
    }
    if (!res.ok) {
      return NextResponse.json({ available: false, reason: `upstream ${res.status}` }, { status: 200 });
    }
    return NextResponse.json(await res.json());
  } catch {
    return NextResponse.json({ available: false, reason: 'fetch error' }, { status: 200 });
  }
}
