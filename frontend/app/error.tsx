"use client";

export default function GlobalError({ reset }: { error: Error; reset: () => void }) {
  return (
    <html lang="en">
      <body className="flex min-h-screen items-center justify-center bg-background p-6">
        <div className="flex flex-col items-center gap-4 text-center">
          <p className="text-lg font-semibold text-foreground">Something went wrong</p>
          <p className="max-w-sm text-sm text-muted">An unexpected error occurred. Please refresh the page.</p>
          <button
            onClick={reset}
            className="rounded-md bg-brand px-4 py-2 text-sm font-medium text-brand-foreground"
          >
            Refresh
          </button>
        </div>
      </body>
    </html>
  );
}
