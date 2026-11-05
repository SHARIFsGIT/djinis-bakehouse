/* The costing desk's pure vocabulary. The backend derives five cost
   statuses (app/costing.py) and this maps each to the label and badge the
   admin surfaces paint — one place, so the review rail, the recipes tab
   and the product cards can never disagree. */

export const COST_STATUSES = {
  not_costed: { label: "Not costed", badge: "badge-ghost" },
  incomplete: { label: "Incomplete", badge: "badge-warning" },
  costed: { label: "Costed", badge: "badge-success" },
  margin_flagged: { label: "Margin flagged", badge: "badge-error" },
  not_computable: { label: "Not computable", badge: "badge-error" },
};

export const costStatusMeta = (status) =>
  COST_STATUSES[status] ?? { label: String(status || "unknown"), badge: "badge-ghost" };

/** The review reasons the cost-review rail speaks (costing.py reasons). */
export const REVIEW_REASONS = {
  no_cost_record: "No cost record yet",
  incomplete: "Incomplete inputs",
  inputs_overdue: "Inputs overdue",
  scheduled: "Scheduled review",
};

/** € per forecast hour for an overhead month — null (not NaN, not Infinity)
   when the figures can't divide. Mirrors the backend's refusal to guess;
   blank inputs are "no figure", never a silent €0/h (Number("") is 0). */
export const overheadRatePerHour = (total, hours) => {
  if (total === "" || total === null || hours === "" || hours === null) return null;
  const t = Number(total);
  const h = Number(hours);
  if (!Number.isFinite(t) || !Number.isFinite(h) || h <= 0) return null;
  return t / h;
};

/** The unit vocabulary the backend's canonical_unit accepts. */
export const UNIT_OPTIONS = ["g", "kg", "ml", "l", "each", "mg"];
