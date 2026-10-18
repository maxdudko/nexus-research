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
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor="pdf" className="text-sm font-medium">
        PDF evidence
      </label>
      <p id="pdf-hint" className="text-sm text-neutral-700">
        Optional. Text PDFs up to 10 MB are indexed before the run starts.
      </p>
      <input
        id="pdf"
        name="pdf"
        type="file"
        accept="application/pdf,.pdf"
        disabled={disabled}
        aria-describedby="pdf-hint"
        className="block w-full text-sm"
        onChange={(event) => {
          const file = event.target.files?.[0] ?? null;
          onFile(file);
        }}
      />
      {fileName !== null && fileSize !== null ? (
        <p>
          {fileName} ({formatBytes(fileSize)})
        </p>
      ) : null}
      {message ? (
        <p role="alert" className="text-sm text-red-800">
          {message}
        </p>
      ) : null}
    </div>
  );
}
