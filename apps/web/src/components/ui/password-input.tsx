"use client";

import { useState, type InputHTMLAttributes } from "react";

type PasswordInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & {
  visibilityLabel?: string;
};

export function PasswordInput({
  visibilityLabel = "password",
  className,
  disabled,
  ...props
}: PasswordInputProps) {
  const [visible, setVisible] = useState(false);
  const label = `${visible ? "Hide" : "Show"} ${visibilityLabel}`;

  return (
    <div className="relative">
      <input
        {...props}
        type={visible ? "text" : "password"}
        disabled={disabled}
        className={["field pr-12", className].filter(Boolean).join(" ")}
      />
      <button
        type="button"
        className="absolute inset-y-1 right-1 flex w-10 items-center justify-center rounded-md text-muted hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
        aria-label={label}
        aria-controls={props.id}
        aria-pressed={visible}
        title={label}
        disabled={disabled}
        onClick={() => setVisible((current) => !current)}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75"
          strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="size-5">
          {visible ? (
            <>
              <path d="m3 3 18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 5.2A10.8 10.8 0 0 1 12 5c5 0 9 7 9 7a19 19 0 0 1-2.7 3.4M6.2 6.2A22 22 0 0 0 3 12s4 7 9 7a10 10 0 0 0 5.8-2.2" />
            </>
          ) : (
            <>
              <path d="M3 12s4-7 9-7 9 7 9 7-4 7-9 7-9-7-9-7Z" />
              <circle cx="12" cy="12" r="3" />
            </>
          )}
        </svg>
      </button>
    </div>
  );
}
