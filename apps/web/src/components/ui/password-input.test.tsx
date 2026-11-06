// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { PasswordInput } from "@/components/ui/password-input";

afterEach(cleanup);

it("toggles independently without submitting or changing the password", async () => {
  const submit = vi.fn((event) => event.preventDefault());
  render(
    <form onSubmit={submit}>
      <label htmlFor="password">Password</label>
      <PasswordInput id="password" name="password" autoComplete="new-password" />
      <label htmlFor="confirmation">Confirm password</label>
      <PasswordInput id="confirmation" visibilityLabel="confirm password" />
    </form>,
  );
  const user = userEvent.setup();
  const password = screen.getByLabelText("Password") as HTMLInputElement;
  const confirmation = screen.getByLabelText("Confirm password") as HTMLInputElement;
  await user.type(password, "my-password");
  expect(password.type).toBe("password");
  await user.click(screen.getByRole("button", { name: "Show password" }));
  expect(password.type).toBe("text");
  expect(password.value).toBe("my-password");
  expect(password.autocomplete).toBe("new-password");
  expect(confirmation.type).toBe("password");
  expect(screen.getByRole("button", { name: "Hide password" }).getAttribute("aria-pressed")).toBe("true");
  await user.keyboard("{Enter}");
  expect(password.type).toBe("password");
  expect(submit).not.toHaveBeenCalled();
});

it("disables the visibility toggle when the field is disabled", () => {
  render(<PasswordInput id="password" disabled required minLength={12} />);
  expect((screen.getByRole("button", { name: "Show password" }) as HTMLButtonElement).disabled).toBe(true);
});
