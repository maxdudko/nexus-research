import { ChangePasswordForm } from "@/components/auth/change-password-form";

export default function AccountPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">Account</h1>
      <section className="surface flex flex-col gap-4 p-6" aria-labelledby="change-password-title">
        <h2 id="change-password-title" className="text-lg font-semibold">Change password</h2>
        <ChangePasswordForm />
      </section>
    </div>
  );
}
