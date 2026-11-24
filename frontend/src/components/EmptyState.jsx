/** `as` is the heading's level: h1 when the empty state is all a page has to
 *  say, one below the page's own heading otherwise. The look is the same. */
export default function EmptyState({ title, hint, children, as: Heading = "h3" }) {
  return (
    <div className="flex flex-col items-center gap-3 py-16 text-center">
      <span className="h-px w-16 bg-base-300" aria-hidden="true" />
      <Heading className="font-display text-2xl font-semibold">{title}</Heading>
      {hint && <p className="max-w-sm text-sm opacity-70">{hint}</p>}
      {children && <div className="mt-2">{children}</div>}
    </div>
  );
}
