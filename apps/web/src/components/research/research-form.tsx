"use client";

import type { ResearchDepth } from "@/lib/types";

import { PdfUpload } from "@/components/research/pdf-upload";
import { Button } from "@/components/ui/button";
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
  onDepth,
  onFile,
  onStart,
  onStop,
  onRetry,
}: {
  state: SessionState;
  onQuestion: (question: string) => void;
  onDepth?: (depth: ResearchDepth) => void;
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
      className="surface flex flex-col gap-4 p-5"
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
          className="field"
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
      <div className="flex flex-col gap-2">
        <label htmlFor="depth" className="text-sm font-medium">Research depth</label>
        <select id="depth" className="field" value={state.depth} disabled={busy}
          onChange={(event) => onDepth?.(event.target.value as ResearchDepth)}>
          <option value="standard">Standard · up to 3 minutes</option>
          <option value="deep">Deep · up to 10 minutes</option>
        </select>
        <p className="text-sm text-muted">Deep research examines more sources and evidence. It uses more provider credits.</p>
      </div>
      <PdfUpload
        fileName={state.fileName}
        fileSize={state.fileSize}
        disabled={busy}
        message={uploadMessage}
        onFile={onFile}
      />
      <div className="flex flex-col gap-3">
        <Button type="submit" variant="primary" disabled={!canStart(state)}>
          Start research
        </Button>
        {busy ? (
          <div className="flex flex-col gap-2">
            <Button type="button" variant="secondary" className="whitespace-normal text-left" onClick={onStop}>
              Stop this request. Stopping does not pause or resume, it can take a moment, and
              provider cost may already have been incurred.
            </Button>
            <p className="text-sm text-muted">
              Closing the tab stops the request. This page cannot warn you before the browser
              closes it.
            </p>
          </div>
        ) : null}
        {canRetry(state.phase) ? (
          <Button type="button" variant="secondary" onClick={onRetry}>
            Start a new run
          </Button>
        ) : null}
      </div>
    </form>
  );
}
