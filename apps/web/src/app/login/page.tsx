import { Suspense } from "react";

import { LoginForm } from "@/components/auth/login-form";
import { ThemeToggle } from "@/components/theme-toggle/theme-toggle";

export default function LoginPage() {
  return (
    <div className="relative flex min-h-dvh items-center justify-center px-4 py-16">
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>
      <div className="surface flex w-full max-w-md flex-col gap-6 p-6 sm:p-8">
        <header className="flex flex-col gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">Nexus Research</h1>
          <p className="text-sm text-muted">Sign in with the account an operator created for you.</p>
        </header>
        <main>
          <Suspense fallback={<p role="status">Checking session.</p>}>
            <LoginForm />
          </Suspense>
        </main>
      </div>
    </div>
  );
}
