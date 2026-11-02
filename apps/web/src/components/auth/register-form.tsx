"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { fetchIdentity } from "@/lib/identity";
import { destinationFromNext, isAllowlistedPath } from "@/lib/return-path";

export function RegisterForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void fetchIdentity().then((result) => {
      if (cancelled) return;
      if (result.status === "ready") {
        router.replace(destinationFromNext(next));
        return;
      }
      if (result.status === "unavailable") {
        setError("The research service is unavailable.");
      }
      setReady(true);
    });
    return () => {
      cancelled = true;
    };
  }, [next, router]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setError(null);
    if (password !== confirmation) {
      setError("Passwords do not match.");
      return;
    }
    setPending(true);
    try {
      const registration = await fetch("/api/v1/auth/register", {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!registration.ok) {
        if (registration.status === 409) {
          setError("This email is unavailable. Try signing in.");
        } else if (registration.status === 422) {
          setError(
            "Enter a valid email and a password of 12–128 characters that differs from your email.",
          );
        } else if (registration.status === 429) {
          const retryAfter = registration.headers.get("retry-after");
          const seconds = retryAfter ? Number(retryAfter) : NaN;
          setError(
            Number.isFinite(seconds) && seconds > 0
              ? `Too many registration attempts. Try again in ${Math.ceil(seconds)} seconds.`
              : "Too many registration attempts. Please try again later.",
          );
        } else {
          setError("The research service is unavailable.");
        }
        return;
      }
      setPassword("");
      setConfirmation("");
      const me = await fetchIdentity();
      if (me.status === "anonymous") {
        setError(
          "Your account was created, but the session could not be confirmed. Please sign in.",
        );
        return;
      }
      if (me.status !== "ready") {
        setError("The research service is unavailable.");
        return;
      }
      router.replace(destinationFromNext(next));
    } catch {
      setError("The research service is unavailable.");
    } finally {
      setPending(false);
    }
  }

  if (!ready) {
    return <p role="status">Checking session.</p>;
  }

  return (
    <form className="flex flex-col gap-4" onSubmit={(event) => void onSubmit(event)}>
      <div className="flex flex-col gap-2">
        <label htmlFor="email" className="text-sm font-medium">
          Email
        </label>
        <input
          id="email"
          name="email"
          type="email"
          autoComplete="email"
          maxLength={254}
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          className="field"
          required
        />
      </div>
      <div className="flex flex-col gap-2">
        <label htmlFor="password" className="text-sm font-medium">
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="new-password"
          minLength={12}
          maxLength={128}
          aria-describedby="password-help"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className="field"
          required
        />
      </div>
      <p id="password-help" className="text-sm text-muted">
        Use 12–128 characters. Your password must differ from your email.
      </p>
      <div className="flex flex-col gap-2">
        <label htmlFor="confirmation" className="text-sm font-medium">
          Confirm password
        </label>
        <input
          id="confirmation"
          name="confirmation"
          type="password"
          autoComplete="new-password"
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
          className="field"
          required
          minLength={12}
          maxLength={128}
        />
      </div>
      {error ? (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      ) : null}
      <p className="text-sm text-muted">
        Already have an account?{" "}
        <Link
          className="underline"
          href={next && isAllowlistedPath(next) ? `/login?next=${encodeURIComponent(next)}` : "/login"}
        >
          Sign in
        </Link>
      </p>
      <Button type="submit" variant="primary" disabled={pending}>
        Create account
      </Button>
    </form>
  );
}
