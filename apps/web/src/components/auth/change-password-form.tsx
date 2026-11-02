"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { csrfHeaders } from "@/lib/csrf";
import { loginHref } from "@/lib/return-path";

export function ChangePasswordForm() {
  const router = useRouter();
  const submitting = useRef(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function clearPasswords() {
    setCurrentPassword("");
    setNewPassword("");
    setConfirmation("");
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    setError(null);
    if (newPassword !== confirmation) {
      setError("New passwords do not match.");
      return;
    }
    if (newPassword === currentPassword) {
      setError("Choose a password different from your current password.");
      return;
    }
    submitting.current = true;
    setPending(true);
    try {
      const response = await fetch("/api/v1/auth/change-password", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "content-type": "application/json", ...csrfHeaders() },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      if (response.ok) {
        clearPasswords();
        router.replace("/login?password_changed=1");
        return;
      }
      if (response.status === 401) {
        clearPasswords();
        router.replace(loginHref("/account"));
        return;
      }
      if (response.status === 400) {
        setError("Your current password is incorrect.");
      } else if (response.status === 422) {
        setError("Use 12–128 characters, different from your email and current password.");
      } else if (response.status === 403) {
        setError("The request was rejected. Reload this page and try again.");
      } else if (response.status === 429) {
        const retryAfter = response.headers.get("retry-after");
        const seconds = retryAfter ? Number(retryAfter) : NaN;
        setError(Number.isFinite(seconds) && seconds > 0
          ? `Too many password change attempts. Try again in ${Math.ceil(seconds)} seconds.`
          : "Too many password change attempts. Please try again later.");
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

  return (
    <form className="flex max-w-md flex-col gap-4" onSubmit={(event) => void onSubmit(event)}>
      <p className="text-sm text-muted">
        Changing your password signs you out on every device, including this browser.
      </p>
      <div className="flex flex-col gap-2">
        <label htmlFor="current-password" className="text-sm font-medium">Current password</label>
        <input id="current-password" name="current_password" type="password" autoComplete="current-password"
          className="field" required maxLength={128} disabled={pending}
          value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} />
      </div>
      <div className="flex flex-col gap-2">
        <label htmlFor="new-password" className="text-sm font-medium">New password</label>
        <input id="new-password" name="new_password" type="password" autoComplete="new-password"
          className="field" required minLength={12} maxLength={128} disabled={pending}
          aria-describedby="password-help" value={newPassword}
          onChange={(event) => setNewPassword(event.target.value)} />
        <p id="password-help" className="text-sm text-muted">
          Use 12–128 characters, different from your email and current password.
        </p>
      </div>
      <div className="flex flex-col gap-2">
        <label htmlFor="confirm-password" className="text-sm font-medium">Confirm new password</label>
        <input id="confirm-password" name="confirmation" type="password" autoComplete="new-password"
          className="field" required minLength={12} maxLength={128} disabled={pending}
          value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
      </div>
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      <Button type="submit" variant="primary" disabled={pending}>
        {pending ? "Changing password…" : "Change password"}
      </Button>
    </form>
  );
}
