"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { PasswordInput } from "@/components/ui/password-input";
import { Button } from "@/components/ui/button";

export function ResetPasswordForm() {
  const router = useRouter();
  const captured = useRef<string | null>(null);
  const submitting = useRef(false);
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [pending, setPending] = useState(false);
  const [complete, setComplete] = useState(false);
  const [invalid, setInvalid] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (captured.current === null) {
      const value = new URLSearchParams(window.location.hash.slice(1)).get("token") ?? "";
      captured.current = /^[A-Za-z0-9_-]{43}$/.test(value) ? value : "";
    }
    window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
    // Retain the captured value through Strict Mode's effect replay.
    const value = captured.current;
    queueMicrotask(() => { setToken(value || null); setReady(true); });
  }, []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current || !token) return;
    setError(null);
    if (password !== confirmation) {
      setError("Passwords do not match.");
      return;
    }
    submitting.current = true;
    setPending(true);
    try {
      const response = await fetch("/api/v1/auth/reset-password", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token, new_password: password }),
      });
      if (response.ok) {
        setComplete(true);
        setPassword(""); setConfirmation(""); setToken(null); captured.current = "";
        router.replace("/login?password_changed=1");
      } else if (response.status === 400) {
        setInvalid(true); setToken(null); captured.current = "";
        setPassword(""); setConfirmation("");
      } else if (response.status === 422) {
        setError("Use 12–128 characters, different from your email and current password.");
      } else if (response.status === 429) {
        setError("Too many reset attempts. Please try again later.");
      } else {
        setError("Password recovery is unavailable. Please try again later.");
      }
    } catch {
      setError("Password recovery is unavailable. Please try again later.");
    } finally {
      submitting.current = false;
      setPending(false);
    }
  }

  if (!ready) return <p role="status">Checking reset link.</p>;
  if (complete) return <p role="status">Password reset. Returning to login.</p>;
  if (!token || invalid) return (
    <div className="flex flex-col gap-4">
      <p role="alert">This reset link is invalid or expired. Request a new link.</p>
      <Link className="underline" href="/forgot-password">Request a new reset link</Link>
    </div>
  );
  return (
    <form className="flex flex-col gap-4" onSubmit={(event) => void onSubmit(event)}>
      <p className="text-sm text-muted">Resetting your password signs you out on every device.</p>
      <label htmlFor="password" className="text-sm font-medium">New password</label>
      <PasswordInput visibilityLabel="password" id="password" name="new_password" autoComplete="new-password"
        required minLength={12} maxLength={128} disabled={pending} aria-describedby="password-help"
        value={password} onChange={(event) => setPassword(event.target.value)} />
      <p id="password-help" className="text-sm text-muted">Use 12–128 characters, different from your email and current password.</p>
      <label htmlFor="confirmation" className="text-sm font-medium">Confirm new password</label>
      <PasswordInput visibilityLabel="confirm password" id="confirmation" name="confirmation" autoComplete="new-password"
        required minLength={12} maxLength={128} disabled={pending} className="field"
        value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      <Button type="submit" variant="primary" disabled={pending}>{pending ? "Resetting…" : "Reset password"}</Button>
      <Link className="text-sm underline" href="/login">Back to login</Link>
    </form>
  );
}
