import { useState } from "react";
import { Link } from "react-router";
import client, { apiError } from "../api/client";

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
    <div className="mx-auto flex max-w-md flex-col px-4 py-16">
      <div className="rise-in rounded-box glass p-8 shadow-lg ring-1 ring-base-300/60">
        <img src="/static/site/logo.png" alt="" className="mx-auto h-14 w-14 object-contain" />
        <h1 className="font-display mt-4 text-center text-3xl font-semibold">Forgot your password?</h1>
        <p className="mt-1 text-center text-sm opacity-70">
          Tell us the email on the account and we'll send a link to set a new one.
        </p>

        {sent ? (
          <div className="mt-6 space-y-4">
            <div className="rounded-box border border-success/40 bg-success/10 p-4 text-sm">
              <p className="font-semibold">{sent.message}</p>
              {sent.reset_link && (
                <p className="mt-2 break-all">
                  Dev shortcut (not sent in production):{" "}
                  <a href={sent.reset_link} className="link link-secondary">
                    open the reset link
                  </a>
                </p>
              )}
            </div>
            <p className="text-center text-sm opacity-70">
              Remembered it after all?{" "}
              <Link to="/login" className="link link-secondary font-semibold">
                Back to log in
              </Link>
            </p>
          </div>
        ) : (
          <form onSubmit={submit} className="mt-6 space-y-4">
            <div>
              <label className="label text-xs" htmlFor="forgot-email">Email</label>
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
            {error && <p className="text-sm text-error">{error}</p>}
            <button type="submit" className="btn btn-primary btn w-full" disabled={busy}>
              {busy ? <span className="loading loading-spinner loading-sm" /> : "Send reset link"}
            </button>
            <p className="text-center text-sm opacity-70">
              Remembered it after all?{" "}
              <Link to="/login" className="link link-secondary font-semibold">
                Log in
              </Link>
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
