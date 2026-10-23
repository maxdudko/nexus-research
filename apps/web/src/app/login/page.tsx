import { Suspense } from "react";

import { LoginForm } from "@/components/auth/login-form";
import { ThemeToggle } from "@/components/theme-toggle/theme-toggle";

export default function LoginPage() {
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-8 px-4 py-8">
      <header className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-2">
          <h1 className="text-2xl font-semibold">Nexus Research</h1>
          <p className="text-sm text-muted">Sign in with the account an operator created for you.</p>
        </div>
        <ThemeToggle />
      </header>
      <main>
        <Suspense fallback={<p role="status">Checking session.</p>}>
          <LoginForm />
        </Suspense>
      </main>
    </div>
  );
}
