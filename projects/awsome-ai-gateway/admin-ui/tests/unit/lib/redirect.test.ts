// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

/**
 * `redirectRelative` 의 계약.
 *
 * 이 헬퍼가 존재하는 이유는 lib/redirect.ts 의 주석에 있다 — 요약하면 컨테이너 안에서
 * 절대 URL 을 만들 근거가 둘 다(`request.url`, `Host` 헤더) 신뢰할 수 없기 때문이다.
 * 여기서는 **호출자가 잘못 쓰는 것**까지 막히는지 고정한다.
 */

import { readFileSync } from 'node:fs';
import { basename, resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { redirectRelative } from '@/lib/redirect';

describe('redirectRelative', () => {
  it('Location 은 준 경로 그대로다 (scheme/host 없음)', () => {
    const res = redirectRelative('/api/auth/login');
    expect(res.headers.get('location')).toBe('/api/auth/login');
    expect(res.headers.get('location')).not.toMatch(/^https?:\/\//);
  });

  it('기본 상태코드는 303 — POST 이후에도 GET 으로 이동시킨다', () => {
    // 307 이면 브라우저가 메서드를 보존해 목적지에 다시 POST 한다. 로그아웃/폼 제출
    // 경로에서 그건 틀린 동작이다.
    expect(redirectRelative('/').status).toBe(303);
  });

  it('상태코드를 지정할 수 있다 (GET→GET 은 307)', () => {
    expect(redirectRelative('/403', { status: 307 }).status).toBe(307);
  });

  it('본문이 없다', async () => {
    expect(await redirectRelative('/').text()).toBe('');
  });

  it('추가 헤더를 실을 수 있고 Location 을 덮어쓰지 않는다', () => {
    const res = redirectRelative('/', { headers: { 'Cache-Control': 'no-store' } });
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(res.headers.get('location')).toBe('/');
  });

  it('쿠키 API 가 그대로 동작한다 (리다이렉트에 Set-Cookie 를 실어야 한다)', () => {
    // 로그인/로그아웃은 리다이렉트 응답에 admin_jwt 를 세팅/삭제한다. NextResponse 를
    // 직접 만들면서 이 기능을 잃으면 인증 흐름이 조용히 깨진다.
    const res = redirectRelative('/');
    res.cookies.set('admin_jwt', 'v', { httpOnly: true, path: '/' });
    expect(res.headers.get('set-cookie') ?? '').toContain('admin_jwt=v');
  });

  describe('오픈 리다이렉트 방지', () => {
    // ⚠️ 지금 호출자는 전부 코드 상수를 넘기므로 도달 불가한 경로다. 그래도 막아 둔다 —
    //    나중에 `?next=` 같은 값을 그대로 넘기는 순간 취약점이 되고, 그때는 아무도
    //    이 함수가 검사를 안 한다는 걸 기억하지 못한다.
    it.each([
      ['//evil.example.com', '스킴 상대 URL — 브라우저가 외부 호스트로 간다'],
      ['/\\evil.example.com', '역슬래시 변형 — 일부 브라우저가 // 처럼 다룬다'],
      ['https://evil.example.com', '절대 URL'],
      ['api/auth/login', '슬래시로 시작하지 않음 — 현재 경로 기준 상대라 예측 불가'],
      ['', '빈 문자열'],
    ])('%s 를 거부한다 (%s)', (path) => {
      expect(() => redirectRelative(path)).toThrow(/same-origin/);
    });

    it('정상 경로는 거부하지 않는다 (과잉차단 대조군)', () => {
      for (const p of ['/', '/403', '/api/auth/login', '/budgets?q=1']) {
        expect(() => redirectRelative(p)).not.toThrow();
      }
    });
  });
});

/**
 * 같은 오리진 리다이렉트가 전부 이 헬퍼를 쓰는지 소스로 대조한다.
 *
 * 한 곳이라도 `NextResponse.redirect` 로 절대 URL 을 만들면 CloudFront 뒤에서 그 경로만
 * 내부 호스트로 새어나간다 — 그런 부분 회귀는 테스트로 잡히지 않고 배포 후에 드러난다.
 */
describe('같은 오리진 리다이렉트는 모두 redirectRelative 를 쓴다', () => {
  function uiRoot(): string {
    const cwd = process.cwd();
    if (basename(cwd) !== 'admin-ui') {
      throw new Error(`cwd 가 admin-ui 가 아니다(${cwd})`);
    }
    return cwd;
  }

  const FILES = [
    'src/middleware.ts',
    'src/app/api/auth/callback/route.ts',
    'src/app/api/auth/logout/route.ts',
    'src/app/api/auth/dev-login/route.ts',
    'src/app/api/auth/login/route.ts',
  ];

  /** 주석을 걷어낸다 — 주석의 `NextResponse.redirect` 언급은 증거가 아니다. */
  function stripComments(src: string): string {
    return src
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .split('\n')
      .filter((l) => !l.trim().startsWith('//'))
      .join('\n');
  }

  it.each(FILES)('%s', (rel) => {
    const src = readFileSync(resolve(uiRoot(), rel), 'utf-8');
    expect(src.length).toBeGreaterThan(200); // 대조군: 경로가 맞는가
    const code = stripComments(src);

    const absolute = [...code.matchAll(/NextResponse\.redirect\(([^)]*)\)/g)].map((m) => m[1]);

    // ⚠️ 정확히 비교한다. `endsWith('login/route.ts')` 로 쓰면 **dev-login/route.ts 도
    //    걸려서**(같은 접미사) 그 파일이 IdP 분기를 타고 거짓 실패한다 — 실제로 그렇게
    //    한 번 틀렸다.
    if (rel === 'src/app/api/auth/login/route.ts') {
      // IdP authorize 로 나가는 리다이렉트는 **절대 URL 이어야** 한다(외부 오리진).
      expect(absolute).toHaveLength(1);
      expect(absolute[0]).toContain('authorizeUrl');
    } else if (rel === 'src/app/api/auth/logout/route.ts') {
      // OIDC 배포의 로그아웃은 IdP /logout 으로 나가야 한다 — 로컬 쿠키만 지우면
      // IdP 세션이 살아 있어 즉시 재로그인된다. 그 한 곳의 절대 URL 을 허용한다.
      expect(absolute.length).toBeLessThanOrEqual(1);
      if (absolute.length === 1) expect(absolute[0]).toContain('url.toString');
    } else if (rel === 'src/middleware.ts') {
      // middleware 는 예외: Next.js 14.2 어댑터가 Location 을 다시 파싱하므로 상대 경로면
      // 500 이다. 대신 오리진을 request.url 이 아닌 헤더(externalOrigin)로 만든 절대 URL
      // 하나만 허용한다.
      expect(absolute).toHaveLength(1);
      expect(absolute[0]).toContain('externalOrigin(');
    } else {
      expect(absolute).toEqual([]);
    }
  });

  it('주석 제거가 no-op 이 아니다 (대조군)', () => {
    const src = readFileSync(resolve(uiRoot(), 'src/lib/redirect.ts'), 'utf-8');
    // 이 파일은 주석에서만 NextResponse.redirect 를 언급한다.
    expect(src).toContain('NextResponse.redirect');
    expect(stripComments(src)).not.toContain('NextResponse.redirect');
  });
});
