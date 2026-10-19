"use client";

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function PdfUpload({
  fileName,
  fileSize,
  disabled,
  message,
  onFile,
}: {
  fileName: string | null;
  fileSize: number | null;
  disabled: boolean;
  message: string | null;
  onFile: (file: File | null) => void;
}) {
  const selected = fileName !== null && fileSize !== null;

  return (
    <div className="flex flex-col gap-2">
      <label htmlFor="pdf" className="text-sm font-medium">
        PDF evidence
      </label>
      <p id="pdf-hint" className="text-sm text-muted">
        Optional. Text PDFs up to 10 MB are indexed before the run starts.
      </p>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <input
          id="pdf"
          name="pdf"
          type="file"
          accept="application/pdf,.pdf"
          disabled={disabled}
          aria-describedby={selected ? "pdf-hint pdf-file" : "pdf-hint"}
          className="w-auto cursor-pointer text-[0px] file:cursor-pointer file:text-sm"
          onChange={(event) => {
            const file = event.target.files?.[0] ?? null;
            onFile(file);
          }}
        />
        <p
          id="pdf-file"
          aria-hidden={selected ? undefined : true}
          className={`min-w-0 text-sm break-all ${disabled ? "text-disabled" : selected ? "" : "text-muted"}`}
        >
          {selected ? `${fileName} (${formatBytes(fileSize)})` : "No file chosen"}
        </p>
      </div>
      {message ? (
        <p role="alert" className="text-sm text-danger">
          {message}
        </p>
      ) : null}
    </div>
  );
}
