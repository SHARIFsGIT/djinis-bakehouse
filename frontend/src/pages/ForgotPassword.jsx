import { useState } from "react";
import { Link } from "react-router";
import client, { apiError } from "../api/client";
import AuthLayout from "../components/AuthLayout";

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(null); // { message, reset_link? }
  const [error, setError] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const { data } = await client.post("/auth/forgot-password", { email });
      setSent(data);
    } catch (err) {
      setError(apiError(err, "Couldn't send the reset link"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout>
      <h1 className="font-display text-4xl leading-[1.05] font-semibold text-balance sm:text-5xl">
        {/* The question mark rides inside the italic: set upright after
            it, the italic d leans into it. */}
        Forgot your <em className="text-secondary italic">password?</em>
      </h1>
      {!sent && (
        <p className="mt-3 text-lg leading-relaxed text-pretty opacity-80">
          Enter the account's email — we'll send a reset link.
        </p>
      )}

      {sent ? (
        <div className="mt-8 rounded-box border border-success/40 bg-success/10 p-4 text-sm">
          <p>{sent.message}</p>
          {sent.reset_link && (
            <p className="mt-2 break-all">
              Dev shortcut (not sent in production):{" "}
              <a href={sent.reset_link} className="link link-secondary">
                open the reset link
              </a>
            </p>
          )}
        </div>
      ) : (
        <form onSubmit={submit} className="mt-8 space-y-5">
          <div>
            <label className="label text-sm" htmlFor="forgot-email">Email</label>
            <input
              id="forgot-email"
              type="email"
              className="input input-bordered w-full"
              placeholder="you@example.com"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          {error && <p className="text-sm font-semibold text-error">{error}</p>}
          <button type="submit" className="btn btn-primary btn-lg w-full" disabled={busy}>
            {busy ? <span className="loading loading-spinner loading-sm" /> : "Send reset link"}
          </button>
          {/* An account opened with a phone number has no mailbox for a
              link to land in — the bakery hands one over instead. */}
          <p className="rounded-box bg-base-200/60 p-4 text-sm leading-relaxed">
            Signed up with a phone number and no email? Get in touch with the bakery —{" "}
            <Link to="/contact" className="link link-secondary font-semibold">
              find us here
            </Link>{" "}
            — and we&rsquo;ll send you a reset link.
          </p>
        </form>
      )}

      <p className="dash-divider mt-8 pt-6 text-sm">
        <Link to="/login" className="link link-secondary font-semibold">
          Back to log in
        </Link>
      </p>
    </AuthLayout>
  );
}
