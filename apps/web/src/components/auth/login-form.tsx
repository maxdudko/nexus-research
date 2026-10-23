"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { csrfHeaders } from "@/lib/csrf";

interface Identity {
  email: string;
}

export function LoginForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      const login = await fetch("/api/v1/auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!login.ok) {
        setError("The email or password is incorrect.");
        return;
      }
      const me = await fetch("/api/v1/auth/me", { credentials: "same-origin" });
      if (!me.ok) {
        setError("The email or password is incorrect.");
        return;
      }
      const body: unknown = await me.json();
      if (!isIdentity(body)) {
        setError("The email or password is incorrect.");
        return;
      }
      setIdentity({ email: body.email });
      setPassword("");
    } catch {
      setError("The research service is unavailable.");
    } finally {
      setPending(false);
    }
  }

  async function onLogout() {
    await fetch("/api/v1/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders(),
    });
    setIdentity(null);
    router.replace("/login");
  }

  if (identity) {
    return (
      <section className="flex flex-col gap-4">
        <p className="text-sm text-muted">
          Signed in as <span className="text-ink">{identity.email}</span>
        </p>
        <button
          type="button"
          onClick={() => void onLogout()}
          className="w-fit cursor-pointer rounded border border-line-strong px-4 py-2"
        >
          Log out
        </button>
        <Link href="/" className="text-sm text-ink underline">
          Continue to research
        </Link>
      </section>
    );
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
          autoComplete="username"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          className="w-full rounded border border-line bg-surface p-3 text-base text-ink"
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
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className="w-full rounded border border-line bg-surface p-3 text-base text-ink"
          required
        />
      </div>
      {error ? (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      ) : null}
      <button
        type="submit"
        disabled={pending}
        className="w-fit cursor-pointer rounded bg-primary px-4 py-2 text-on-primary"
      >
        Log in
      </button>
    </form>
  );
}

function isIdentity(value: unknown): value is Identity {
  if (typeof value !== "object" || value === null || !("email" in value)) return false;
  return typeof value.email === "string" && value.email.length > 0;
}
