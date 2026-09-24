'use client';

// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

import { useState, type FormEvent } from 'react';
import { useRouter } from 'next/navigation';

interface LoginFormProps {
  /** 로컬 개발 환경(DEV_LOGIN_ENABLED=true)에서만 표시되는 dev-login 바로가기. */
  devLoginEnabled: boolean;
}

interface ChallengeState {
  cognitoSession: string;
}

const CARD_INPUT_CLASS =
  'rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring';
const SUBMIT_BUTTON_CLASS =
  'mt-2 inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow hover:bg-primary/90 transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50';

export function LoginForm({ devLoginEnabled }: LoginFormProps) {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  // Cognito 계정이 임시 비밀번호(FORCE_CHANGE_PASSWORD) 상태면 /api/auth/login 이
  // 토큰 대신 챌린지를 반환한다 — 이 상태가 세팅되면 아래 새 비밀번호 폼으로 전환.
  const [challenge, setChallenge] = useState<ChallengeState | null>(null);

  async function handleLoginSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);

    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });

      const body = (await res.json().catch(() => ({}))) as {
        error?: string;
        challenge?: string;
        cognito_session?: string;
      };

      if (!res.ok) {
        setError(body.error ?? 'Login failed.');
        return;
      }

      if (body.challenge === 'NEW_PASSWORD_REQUIRED' && body.cognito_session) {
        setChallenge({ cognitoSession: body.cognito_session });
        return;
      }

      router.push('/');
      router.refresh();
    } catch {
      setError('Could not reach the server.');
    } finally {
      setIsSubmitting(false);
    }
  }

  if (challenge) {
    return (
      <NewPasswordForm
        email={email}
        cognitoSession={challenge.cognitoSession}
        onBack={() => setChallenge(null)}
      />
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="glass w-full max-w-sm rounded-apple p-8 shadow-lg">
        <div className="mb-6 text-center">
          <h1 className="text-xl font-bold text-foreground">AWSome AI Gateway</h1>
          <p className="mt-1 text-sm text-muted-foreground">Admin console sign-in</p>
        </div>

        <form onSubmit={handleLoginSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="email" className="text-sm font-medium text-foreground">
              Email
            </label>
            <input
              id="email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={CARD_INPUT_CLASS}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="password" className="text-sm font-medium text-foreground">
              Password
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={CARD_INPUT_CLASS}
            />
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}

          <button type="submit" disabled={isSubmitting} className={SUBMIT_BUTTON_CLASS}>
            {isSubmitting ? 'Signing in...' : 'Sign in'}
          </button>
        </form>

        {devLoginEnabled && (
          <div className="mt-6 border-t border-border pt-4 text-center">
            <a
              href="/api/auth/dev-login"
              className="text-xs text-muted-foreground hover:text-foreground hover:underline"
            >
              Sign in with dev mode (dev-login)
            </a>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Shown after Cognito responds with NEW_PASSWORD_REQUIRED (accounts an admin
 * just created via the console/CLI start with a temporary password). Submits
 * the new password to /api/auth/new-password, which completes the Cognito
 * challenge and finishes the same login flow as a normal sign-in.
 */
function NewPasswordForm({
  email,
  cognitoSession,
  onBack,
}: {
  email: string;
  cognitoSession: string;
  onBack: () => void;
}) {
  const router = useRouter();
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);

    if (newPassword !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }

    setIsSubmitting(true);
    try {
      const res = await fetch('/api/auth/new-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email,
          new_password: newPassword,
          cognito_session: cognitoSession,
        }),
      });

      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { error?: string };
        setError(body.error ?? 'Could not set new password.');
        return;
      }

      router.push('/');
      router.refresh();
    } catch {
      setError('Could not reach the server.');
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="glass w-full max-w-sm rounded-apple p-8 shadow-lg">
        <div className="mb-6 text-center">
          <h1 className="text-xl font-bold text-foreground">Set a new password</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            This account has a temporary password — choose a permanent one for {email}.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="new-password" className="text-sm font-medium text-foreground">
              New password
            </label>
            <input
              id="new-password"
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className={CARD_INPUT_CLASS}
            />
            <p className="text-xs text-muted-foreground">
              At least 12 characters, with uppercase, lowercase, and a number.
            </p>
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="confirm-password" className="text-sm font-medium text-foreground">
              Confirm new password
            </label>
            <input
              id="confirm-password"
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className={CARD_INPUT_CLASS}
            />
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}

          <button type="submit" disabled={isSubmitting} className={SUBMIT_BUTTON_CLASS}>
            {isSubmitting ? 'Setting password...' : 'Set password and sign in'}
          </button>
        </form>

        <div className="mt-6 border-t border-border pt-4 text-center">
          <button
            type="button"
            onClick={onBack}
            className="text-xs text-muted-foreground hover:text-foreground hover:underline"
          >
            Back to sign in
          </button>
        </div>
      </div>
    </div>
  );
}
