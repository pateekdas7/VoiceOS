"use client";

export default function ClientError({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 p-10 text-center">
      <p className="text-base font-semibold text-foreground">Something went wrong</p>
      <p className="max-w-sm text-sm text-muted">{error.message || "An unexpected error occurred."}</p>
      <button
        onClick={reset}
        className="rounded-md border border-border px-4 py-2 text-sm hover:bg-background"
      >
        Try again
      </button>
    </div>
  );
}
