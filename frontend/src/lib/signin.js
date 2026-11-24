/* One field for "email or phone number": what was typed is read here, once,
   so the log-in page and the sign-up page agree on what it is.

   Nothing is decided about whether the number or the address is *real* —
   the server does that. This only tells the two apart, so the right one is
   sent under the right name. */

const PHONE_SHAPE = /^[+0-9][0-9 ()./-]*$/;

const digits = (value) => String(value ?? "").replace(/\D/g, "");

/** True when what was typed reads as a phone number rather than an email. */
export const looksLikePhone = (value) => {
  const typed = String(value ?? "").trim();
  return typed !== "" && !typed.includes("@") && PHONE_SHAPE.test(typed) && digits(typed).length >= 3;
};

/** What was typed, as the API wants it: `{ email }`, `{ phone }`, or null
    when it is neither (too short to be a number, no "@" to be an address). */
export const identify = (value) => {
  const typed = String(value ?? "").trim();
  if (typed.includes("@")) return { email: typed };
  if (PHONE_SHAPE.test(typed) && digits(typed).length >= 7) return { phone: typed };
  return null;
};

/** A number someone will sign in with has to say which country it is from:
    a leading 0 (the shop's own country) or a "+" and the country code.
    Mirrors the server's rule, so the form can say so before sending. */
export const saysItsCountry = (phone) => /^\s*[+0]/.test(String(phone ?? ""));

/* ------------------------------------------------------------------ */
/* The Email | Phone tabs on the log-in and sign-up pages              */
/* ------------------------------------------------------------------ */

/** What was typed, read on a tab that already says which it is: `{ email }`
    from the Email tab, `{ phone }` from the Phone tab — and null when the
    typing doesn't fit its tab (a number under Email, an address under
    Phone), so the page can point to the other tab instead of guessing. */
export const identifyAs = (way, value) => {
  const id = identify(value);
  if (!id) return null;
  return (way === "phone") === "phone" in id ? id : null;
};

/** The words for a typing that doesn't fit its tab, or that is neither. */
export const wayProblem = (way, value, { signingUp = false } = {}) => {
  const typed = String(value ?? "").trim();
  if (way === "email" && looksLikePhone(typed)) return "That looks like a phone number — use the Phone tab.";
  if (way === "phone" && typed.includes("@")) return "That's an email address — use the Email tab.";
  if (way === "email") {
    return signingUp ? "Enter your email address — you'll log in with it." : "Enter the email address on your account.";
  }
  return signingUp ? "Enter your phone number — you'll log in with it." : "Enter the phone number on your account.";
};

export const WAY_KEY = "bakehouse.signin-way";

/** Which tab the pages open on: one asked for in the address (`?with=phone`,
    as the links between the two pages carry it), else the way this browser
    last signed in with, else Email. */
export const initialWay = (search = "") => {
  const asked = new URLSearchParams(search).get("with");
  if (asked === "email" || asked === "phone") return asked;
  try {
    return localStorage.getItem(WAY_KEY) === "phone" ? "phone" : "email";
  } catch {
    return "email";
  }
};

/** Remember the way that just worked — a per-browser nicety, never needed. */
export const rememberWay = (way) => {
  try {
    localStorage.setItem(WAY_KEY, way);
  } catch {
    /* private mode, storage full: the pages simply open on Email */
  }
};
