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
