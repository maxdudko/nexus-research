import { ForgotPasswordForm } from "@/components/auth/forgot-password-form";
import { ThemeToggle } from "@/components/theme-toggle/theme-toggle";

export const metadata = { referrer: "no-referrer" as const };

export default function Page() {
  return (
    <div className="relative flex min-h-dvh items-center justify-center px-4 py-16">
      <div className="absolute top-4 right-4"><ThemeToggle /></div>
      <main className="surface flex w-full max-w-md flex-col gap-6 p-6 sm:p-8">
        <header className="flex flex-col gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">Forgot password?</h1>
          <p className="text-sm text-muted">Enter your email to request a reset link.</p>
        </header>
        <ForgotPasswordForm />
      </main>
    </div>
  );
}
