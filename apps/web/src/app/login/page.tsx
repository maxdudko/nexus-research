import { LoginForm } from "@/components/auth/login-form";

export default function LoginPage() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-col gap-8 px-4 py-8">
      <header className="flex flex-col gap-2 pr-12">
        <h1 className="text-2xl font-semibold">Nexus Research</h1>
        <p className="text-sm text-muted">Sign in with the account an operator created for you.</p>
      </header>
      <LoginForm />
    </main>
  );
}
