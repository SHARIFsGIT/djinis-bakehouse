/* Where the shop delivers — the storefront's side of app/delivery.py. The
   server is the judge (checkout refuses an address outside the area); this
   mirror exists so the customer hears it while typing the postal code, not
   after pressing the pay button. Entries arrive already normalised from
   GET /api/store: exact codes ("10115") and areas ("101*"). */

const normalize = (value) => String(value ?? "").replace(/\s+/g, "").toUpperCase();

/** Whether a postal code is inside the area. No entries = no limit. */
export const deliversTo = (postalCode, entries) => {
  if (!entries?.length) return true;
  const code = normalize(postalCode);
  if (!code) return false;
  return entries.some((entry) =>
    entry.endsWith("*") ? code.startsWith(entry.slice(0, -1)) : code === entry,
  );
};

/** The area in a line a person can read: "10115, 10117, 101… and 12 more". */
export const describeArea = (entries, show = 8) => {
  const list = entries ?? [];
  const names = list
    .slice(0, show)
    .map((entry) => (entry.endsWith("*") ? `${entry.slice(0, -1)}…` : entry));
  const rest = list.length - names.length;
  return names.join(", ") + (rest > 0 ? ` and ${rest} more` : "");
};
