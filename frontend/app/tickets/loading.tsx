export default function Loading() {
  return (
    <div className="container mx-auto max-w-5xl p-6 sm:p-10 animate-pulse space-y-4">
      <div className="flex justify-between">
        <div className="h-9 w-32 rounded-lg bg-muted" />
        <div className="h-9 w-28 rounded-lg bg-muted" />
      </div>
      <div className="flex gap-3">
        {Array.from({ length: 3 }).map((_, i) => <div key={i} className="h-8 w-24 rounded-lg bg-muted" />)}
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => <div key={i} className="h-48 rounded-xl bg-muted" />)}
      </div>
    </div>
  );
}
