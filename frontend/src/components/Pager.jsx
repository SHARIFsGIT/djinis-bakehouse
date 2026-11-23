import { ChevronLeft, ChevronRight } from "lucide-react";

/** Previous · "Page 2 / 5" · next — the one pager every paged list wears,
 *  in the shop and at the desk. (It used to be written out four times, three
 *  of them with arrow characters for buttons and no names for a screen
 *  reader to say.) Nothing at all when there is only one page. */
export default function Pager({ page, pages, onPage, label = "Pages", className = "" }) {
  if (!pages || pages <= 1) return null;
  return (
    <nav className={`flex justify-center ${className}`} aria-label={label}>
      <div className="join">
        <button
          type="button"
          className="btn join-item btn-sm"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
          aria-label="Previous page"
        >
          <ChevronLeft size={16} aria-hidden="true" />
        </button>
        <span className="btn join-item btn-sm figures pointer-events-none" aria-current="page">
          Page {page} / {pages}
        </span>
        <button
          type="button"
          className="btn join-item btn-sm"
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
          aria-label="Next page"
        >
          <ChevronRight size={16} aria-hidden="true" />
        </button>
      </div>
    </nav>
  );
}
