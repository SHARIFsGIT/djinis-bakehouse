export default function EmptyState({ title, hint, children }) {
  return (
    <div className="flex flex-col items-center gap-3 py-16 text-center">
      <span className="h-px w-16 bg-base-300" aria-hidden="true" />
      <h3 className="font-display text-2xl font-semibold">{title}</h3>
      {hint && <p className="max-w-sm text-sm opacity-70">{hint}</p>}
      {children && <div className="mt-2">{children}</div>}
    </div>
  );
}
