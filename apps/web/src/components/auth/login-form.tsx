"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { fetchIdentity } from "@/lib/identity";
import { destinationFromNext } from "@/lib/return-path";

export function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
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
      const me = await fetchIdentity();
      if (me.status === "anonymous") {
        setError("The email or password is incorrect.");
        return;
      }
      if (me.status !== "ready") {
        setError("The research service is unavailable.");
        return;
      }
      setPassword("");
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
