export function Loading({ label = "Warming up the oven…" }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-20">
      <span className="loading loading-bars loading-lg text-primary" />
      <p className="text-sm font-semibold opacity-60">{label}</p>
    </div>
  );
}

/** Placeholder grid shown while products load. */
export function ProductGridSkeleton({ count = 8 }) {
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-4">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="overflow-hidden rounded-box glass shadow-sm ring-1 ring-base-300/60">
          <div className="aspect-[4/3] w-full animate-pulse bg-base-200" />
          <div className="space-y-2 p-4">
            <div className="h-4 w-3/4 animate-pulse rounded bg-base-200" />
            <div className="h-3 w-1/2 animate-pulse rounded bg-base-200" />
            <div className="h-6 w-full animate-pulse rounded bg-base-200" />
          </div>
        </div>
      ))}
    </div>
  );
}
