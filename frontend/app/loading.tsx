export default function Loading() {
  return (
    <div className="container mx-auto max-w-5xl p-6 sm:p-10 space-y-6 animate-pulse">
      <div className="h-9 w-48 rounded-lg bg-muted" />
      <div className="h-4 w-72 rounded bg-muted" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-32 rounded-xl bg-muted" />
        ))}
      </div>
    </div>
  );
}
