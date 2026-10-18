import type { ApiErrorBody } from "@/lib/types";

export const REQUEST_FAILED_MESSAGE = "The request could not be completed.";

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
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

export async function errorFromResponse(response: Response): Promise<ApiError> {
  try {
    const body: unknown = await response.json();
    if (isErrorBody(body)) {
      return new ApiError(body.error.code, body.error.message, response.status);
    }
  } catch {
    // The body is not the error envelope.
  }
  return new ApiError("request_failed", REQUEST_FAILED_MESSAGE, response.status);
}
