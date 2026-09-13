"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { announceAccountChange } from "@/lib/account";
import { ApiError, signIn, signUp } from "@/lib/api/client";
import { ArrowRight } from "@/components/ui/primitives";

/**
 * Sign up or sign in (`REQ-AUTH-003`, Flow E and Flow F).
 *
 * One form for both, because they ask for the same two things and differ only
 * in what the server does with them. The server also brings along any research
 * done on this browser while signed out (`DEC-17`), and the form says so rather
 * than leaving the reader to wonder whether it was saved.
 *
 * The password rule is stated up front and checked here for the immediate
 * feedback; the server's message is what shows if the two ever disagree.
 */

const PASSWORD_MIN = 12;

type Mode = "signin" | "signup";

export function AuthForm({ mode, next }: { mode: Mode; next: string | null }) {
  const router = useRouter();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const signingUp = mode === "signup";
  const shortPassword = signingUp && password.length > 0 && password.length < PASSWORD_MIN;
  const canSubmit =
    email.trim().length > 0 &&
    password.length > 0 &&
    !shortPassword &&
    !submitting;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;

    setSubmitting(true);
    setError(null);
    try {
      await (signingUp ? signUp(email, password) : signIn(email, password));
      announceAccountChange();
      router.push(next ?? "/history");
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : "Could not reach the service. Check your connection and try again.",
      );
      setSubmitting(false);
    }
  }

  const other = signingUp ? "/signin" : "/signup";
  const otherHref = next ? `${other}?next=${encodeURIComponent(next)}` : other;

  return (
    <form onSubmit={onSubmit} className="mt-10 max-w-[28rem]" noValidate>
      <label htmlFor="email" className="claim-label block">
        Email
      </label>
      <input
        id="email"
        name="email"
        type="email"
        autoComplete="email"
        required
        value={email}
        onChange={(event) => setEmail(event.target.value)}
        className="well mt-2 w-full bg-sunk px-4 py-3 text-small text-ink outline-none transition-shadow duration-200 focus:shadow-soft"
      />

      <label htmlFor="password" className="claim-label mt-6 block">
        Password
      </label>
      <input
        id="password"
        name="password"
        type="password"
        autoComplete={signingUp ? "new-password" : "current-password"}
        required
        minLength={signingUp ? PASSWORD_MIN : undefined}
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        aria-describedby={signingUp ? "password-help" : undefined}
        className="well mt-2 w-full bg-sunk px-4 py-3 text-small text-ink outline-none transition-shadow duration-200 focus:shadow-soft"
      />
      {signingUp ? (
        <p id="password-help" className="mt-2 text-micro text-ink-muted">
          {shortPassword
            ? `${PASSWORD_MIN - password.length} more characters.`
            : `At least ${PASSWORD_MIN} characters. There is no password reset yet, so keep it somewhere safe.`}
        </p>
      ) : null}

      {error ? (
        <p
          role="alert"
          className="mt-6 border-l-2 border-ochre-deep pl-4 text-small text-ink-soft"
        >
          {error}
        </p>
      ) : null}

      <div className="mt-8 flex flex-wrap items-center gap-5">
        <button
          type="submit"
          disabled={!canSubmit}
          className="group inline-flex h-12 items-center justify-center gap-2 rounded-sm bg-ink px-6 text-small font-medium text-paper shadow-soft transition-[background-color,box-shadow,transform] duration-200 ease-out hover:bg-ochre-deep hover:shadow-lift active:translate-y-px disabled:cursor-not-allowed disabled:bg-line-strong disabled:text-ink-muted disabled:shadow-none"
        >
          {submitting
            ? signingUp
              ? "Creating account"
              : "Signing in"
            : signingUp
              ? "Create account"
              : "Sign in"}
          {submitting ? null : <ArrowRight />}
        </button>
        <p className="text-micro text-ink-muted">
          {signingUp ? "Already have an account? " : "New here? "}
          <Link
            href={otherHref}
            className="text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ochre-deep"
          >
            {signingUp ? "Sign in" : "Create an account"}
          </Link>
        </p>
      </div>

      <p className="measure mt-10 text-micro text-ink-muted">
        {signingUp
          ? "Research you started on this browser comes with you into the account, and stays private to it."
          : "Research you started on this browser while signed out is added to your account when you sign in."}
      </p>
    </form>
  );
}
