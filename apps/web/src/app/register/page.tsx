import Link from "next/link";

import { Suspense } from "react";

import { RegisterForm } from "@/components/auth/register-form";
import { ThemeToggle } from "@/components/theme-toggle/theme-toggle";

export default function RegisterPage() {
  return (
    <div className="relative flex min-h-dvh items-center justify-center px-4 py-16">
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>
      <div className="surface flex w-full max-w-md flex-col gap-6 p-6 sm:p-8">
        <header className="flex flex-col gap-2">
          <Link href="/" className="text-sm text-muted underline underline-offset-4">Back to Nexus Research</Link>
          <h1 className="text-2xl font-semibold tracking-tight">Create an account</h1>
          <p className="text-sm text-muted">Create an account to start your research.</p>
        </header>
        <main>
          <Suspense fallback={<p role="status">Checking session.</p>}>
            <RegisterForm />
          </Suspense>
        </main>
      </div>
    </div>
  );
}
