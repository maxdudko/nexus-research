"use client";

import { PdfUpload } from "@/components/research/pdf-upload";
import {
  canRetry,
  canStart,
  canStop,
  questionError,
  type SessionState,
} from "@/lib/research-session";

export function ResearchForm({
  state,
  onQuestion,
  onFile,
  onStart,
  onStop,
  onRetry,
}: {
  state: SessionState;
  onQuestion: (question: string) => void;
  onFile: (file: File | null) => void;
  onStart: () => void;
  onStop: () => void;
  onRetry: () => void;
}) {
  const busy = canStop(state.phase);
  const trimmed = state.question.trim().length;
  const invalid = questionError(state.question) !== null;
  const showQuestionError = trimmed > 0 && invalid;
  const uploadMessage =
    state.phase === "upload_failed" || state.phase === "uploading" ? state.errorMessage : null;

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        onStart();
      }}
    >
      <div className="flex flex-col gap-2">
        <label htmlFor="question" className="text-sm font-medium">
          Research question
        </label>
        <textarea
          id="question"
          name="question"
          rows={5}
          value={state.question}
          readOnly={busy}
          aria-invalid={showQuestionError}
          aria-describedby={showQuestionError ? "question-hint question-error" : "question-hint"}
          className="w-full rounded border border-line bg-surface p-3 text-base text-ink"
          onChange={(event) => onQuestion(event.target.value)}
        />
        <p id="question-hint" className="text-sm text-muted">
          Use 10 to 2000 characters.
        </p>
        {showQuestionError ? (
          <p id="question-error" role="alert" className="text-sm text-danger">
            Enter a question between 10 and 2000 characters.
          </p>
        ) : null}
      </div>
      <PdfUpload
        fileName={state.fileName}
        fileSize={state.fileSize}
        disabled={busy}
        message={uploadMessage}
        onFile={onFile}
      />
      {state.researchId === "pending" ? null : (
        <p className="text-sm text-muted" data-research-id={state.researchId}>
          Draft {state.researchId}
        </p>
      )}
      <div className="flex flex-col gap-3">
        <button
          type="submit"
          disabled={!canStart(state)}
          className={`rounded bg-primary px-4 py-2 text-on-primary ${canStart(state) ? 'cursor-pointer' : 'cursor-not-allowed'}`}
        >
          Start research
        </button>
        {busy ? (
          <button
            type="button"
            onClick={onStop}
            className="rounded border border-line-strong px-4 py-2 cursor-pointer"
          >
            Stop this request; research may be interrupted.
          </button>
        ) : null}
        {canRetry(state.phase) ? (
          <button type="button" onClick={onRetry} className="rounded border border-line px-4 py-2 cursor-pointer">
            Start a new run
          </button>
        ) : null}
      </div>
    </form>
  );
}
