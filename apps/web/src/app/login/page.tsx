import Link from "next/link";

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
          <Link href="/" className="text-sm text-muted underline underline-offset-4">Back to Nexus Research</Link>
          <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
          <p className="text-sm text-muted">Sign in to your Nexus Research account.</p>
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
