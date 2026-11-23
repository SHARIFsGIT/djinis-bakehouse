export function Loading({ label = "Warming up the oven…" }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-20">
      <span className="loading loading-bars loading-lg text-secondary" aria-hidden="true" />
      <p className="text-sm font-semibold opacity-70">{label}</p>
    </div>
  );
}

/** A request that failed, said plainly, with a way to try again — so a
 *  page whose first fetch broke never sits on its loader forever. */
export function LoadError({ message = "That didn't load.", onRetry }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center" role="alert">
      <p className="text-sm font-semibold opacity-70">{message}</p>
      {onRetry && (
        <button type="button" className="btn btn-outline btn-sm" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

/** Placeholder grid shown while products load. Each tile is the shape of
 *  the card that replaces it (the same 3:4 photo, the same text block), in
 *  the same columns as the grid it stands in for (`className`) — so the
 *  page does not jump when the bakes arrive. */
export function ProductGridSkeleton({ count = 8, className = "grid-cols-2 md:grid-cols-3 xl:grid-cols-4" }) {
  return (
    <div className={`grid gap-4 ${className}`} aria-hidden="true">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="overflow-hidden rounded-box glass shadow-sm ring-1 ring-base-300/60">
          <div className="aspect-[3/4] w-full animate-pulse bg-base-200" />
          <div className="space-y-2.5 p-4">
            <div className="h-5 w-3/4 animate-pulse rounded bg-base-200" />
            <div className="h-3 w-1/2 animate-pulse rounded bg-base-200" />
            <div className="h-8 w-full animate-pulse rounded bg-base-200" />
          </div>
        </div>
      ))}
    </div>
  );
}
