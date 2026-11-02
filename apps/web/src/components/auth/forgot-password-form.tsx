"use client";

import Link from "next/link";
import { useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";

export function ForgotPasswordForm() {
  const submitting = useRef(false);
  const [email, setEmail] = useState("");
  const [pending, setPending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    submitting.current = true;
    setPending(true);
    setError(null);
    try {
      const response = await fetch("/api/v1/auth/forgot-password", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "content-type": "application/json" }, body: JSON.stringify({ email }),
      });
      if (response.ok) {
        setSent(true);
        setEmail("");
      } else if (response.status === 422) {
        setError("Enter a valid email address.");
      } else if (response.status === 429) {
        setError("Too many reset requests. Please try again later.");
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

  return (
    <div className="flex flex-col gap-4">
      {sent ? <p role="status">If an eligible account exists, you’ll receive a password-reset link.</p> : (
        <form className="flex flex-col gap-4" onSubmit={(event) => void onSubmit(event)}>
          <label htmlFor="email" className="text-sm font-medium">Email</label>
          <input id="email" name="email" type="email" autoComplete="email" required maxLength={254}
            className="field" disabled={pending} value={email} onChange={(event) => setEmail(event.target.value)} />
          {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
          <Button type="submit" variant="primary" disabled={pending}>{pending ? "Sending…" : "Send reset link"}</Button>
        </form>
      )}
      <Link className="text-sm underline" href="/login">Back to login</Link>
    </div>
  );
}
