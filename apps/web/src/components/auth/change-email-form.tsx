"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";
import { useSessionIdentity, useSessionRefresh } from "@/components/auth/session-boundary";
import { PasswordInput } from "@/components/ui/password-input";
import { Button } from "@/components/ui/button";
import { csrfHeaders } from "@/lib/csrf";
import { loginHref } from "@/lib/return-path";

export function ChangeEmailForm() {
  const router = useRouter();
  const identity = useSessionIdentity();
  const refreshIdentity = useSessionRefresh();
  const submitting = useRef(false);
  const [newEmail, setNewEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [committed, setCommitted] = useState(false);
  const [refreshed, setRefreshed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    const result = await refreshIdentity();
    if (result.status === "ready") {
      setRefreshed(true);
    } else if (result.status === "anonymous") {
      router.replace(loginHref("/account"));
    } else {
      setError("Your email was changed, but account details could not be refreshed. Try refreshing again.");
    }
  }

  async function retryRefresh() {
    if (submitting.current) return;
    submitting.current = true;
    setPending(true);
    setError(null);
    try { await refresh(); } finally { submitting.current = false; setPending(false); }
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current || committed) return;
    submitting.current = true;
    setPending(true);
    setError(null);
    try {
      const response = await fetch("/api/v1/auth/change-email", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "content-type": "application/json", ...csrfHeaders() },
        body: JSON.stringify({ new_email: newEmail, current_password: password }),
      });
      if (response.ok) {
        setPassword(""); setNewEmail(""); setCommitted(true);
        await refresh();
      } else if (response.status === 401) {
        setPassword(""); router.replace(loginHref("/account"));
      } else if (response.status === 400) {
        setError("Your current password is incorrect.");
      } else if (response.status === 409) {
        setError("This email is unavailable.");
      } else if (response.status === 422) {
        setError("Enter a valid new email, different from your current email and password.");
      } else if (response.status === 403) {
        setError("The request was rejected. Reload this page and try again.");
      } else if (response.status === 429) {
        const seconds = Number(response.headers.get("retry-after"));
        setError(Number.isFinite(seconds) && seconds > 0
          ? `Too many email change attempts. Try again in ${Math.ceil(seconds)} seconds.`
          : "Too many email change attempts. Please try again later.");
      } else {
        setError("The research service is unavailable. Try again.");
      }
    } catch {
      setError("The research service is unavailable. Try again.");
    } finally {
      submitting.current = false;
      setPending(false);
    }
  }

  if (committed) return (
    <div className="flex flex-col gap-4">
      <p role="status">Your email was changed. Other devices have been signed out.</p>
      {refreshed ? <p className="text-sm text-muted">Current email: {identity?.email}</p> : null}
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      {!refreshed ? <Button disabled={pending} onClick={() => void retryRefresh()}>Refresh account details</Button> : null}
    </div>
  );

  return (
    <form className="flex max-w-md flex-col gap-4" onSubmit={(event) => void onSubmit(event)}>
      <p className="text-sm text-muted">Current email: {identity?.email}</p>
      <p className="text-sm text-muted">
        Your new email immediately becomes your login and recovery address without verification.
        This browser stays signed in; other devices will be signed out.
      </p>
      <label htmlFor="new-email" className="text-sm font-medium">New email</label>
      <input id="new-email" name="new_email" type="email" autoComplete="email" required maxLength={254}
        className="field" disabled={pending} value={newEmail} onChange={(event) => setNewEmail(event.target.value)} />
      <label htmlFor="email-current-password" className="text-sm font-medium">Current password</label>
      <PasswordInput id="email-current-password" name="current_password" autoComplete="current-password"
        visibilityLabel="current password for email change" required maxLength={128} disabled={pending}
        value={password} onChange={(event) => setPassword(event.target.value)} />
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      <Button type="submit" variant="primary" disabled={pending}>{pending ? "Changing email…" : "Change email"}</Button>
    </form>
  );
}
