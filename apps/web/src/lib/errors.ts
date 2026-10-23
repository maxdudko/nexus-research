import type { ApiErrorBody } from "@/lib/types";

export const REQUEST_FAILED_MESSAGE = "The request could not be completed.";

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly retryAfter: number | null;

  constructor(code: string, message: string, status: number, retryAfter: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== "object" || value === null || !("error" in value)) {
    return false;
  }
  const error = value.error;
  if (typeof error !== "object" || error === null) {
    return false;
  }
  if (!("code" in error) || !("message" in error)) {
    return false;
  }
  return (
    typeof error.code === "string" &&
    error.code.length > 0 &&
    typeof error.message === "string" &&
    error.message.length > 0
  );
}

function retryAfterSeconds(response: Response): number | null {
  const header = response.headers.get("Retry-After");
  if (!header || !/^\d+$/.test(header)) return null;
  return Number(header);
}

function withRetryAfter(message: string, retryAfter: number | null): string {
  if (retryAfter === null) return message;
  return `${message} Try again in ${retryAfter} seconds.`;
}

export async function errorFromResponse(response: Response): Promise<ApiError> {
  const retryAfter = retryAfterSeconds(response);
  try {
    const body: unknown = await response.json();
    if (isErrorBody(body)) {
      return new ApiError(
        body.error.code,
        withRetryAfter(body.error.message, retryAfter),
        response.status,
        retryAfter,
      );
    }
  } catch {
    // The body is not the error envelope.
  }
  return new ApiError(
    "request_failed",
    withRetryAfter(REQUEST_FAILED_MESSAGE, retryAfter),
    response.status,
    retryAfter,
  );
}
