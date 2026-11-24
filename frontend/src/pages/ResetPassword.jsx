import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import client, { apiError } from "../api/client";
import AuthLayout from "../components/AuthLayout";
import PasswordInput from "../components/PasswordInput";
import { useToast } from "../context/ToastContext";

export default function ResetPassword() {
  const toast = useToast();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const token = params.get("token") || "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    if (password !== confirm) {
      setError("The two passwords don't match");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await client.post("/auth/reset-password", { token, password });
      toast.success("Password updated — log in with the new one");
      navigate("/login", { replace: true });
    } catch (err) {
      setError(apiError(err, "Couldn't reset the password"));
    } finally {
      setBusy(false);
    }
  };

  const mismatch = confirm !== "" && password !== confirm;

  return (
    <AuthLayout>
      <h1 className="font-display text-4xl leading-[1.05] font-semibold text-balance sm:text-5xl">
        Reset your <em className="text-secondary italic">password</em>
      </h1>
      <p className="mt-3 text-lg leading-relaxed text-pretty opacity-80">
        Pick something fresh — eight characters or more.
      </p>

      {!token ? (
        <div className="mt-8 rounded-box border border-error/40 bg-error/10 p-4 text-sm">
          <p className="font-semibold">This reset link is missing its key.</p>
          <p className="mt-1">
            Start again from{" "}
            <Link to="/forgot-password" className="link link-secondary font-semibold">
              forgot password
            </Link>{" "}
            and use the link exactly as you received it.
          </p>
        </div>
      ) : (
        <form onSubmit={submit} className="mt-8 space-y-5">
          <div>
            <label className="label text-sm" htmlFor="new-password">New password</label>
            <PasswordInput
              id="new-password"
              placeholder="At least 8 characters"
              autoComplete="new-password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <div>
            <label className="label text-sm" htmlFor="confirm-password">Confirm password</label>
            <PasswordInput
              id="confirm-password"
              placeholder="Once more"
              className={mismatch ? "input-error" : ""}
              autoComplete="new-password"
              required
              minLength={8}
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
            {mismatch && (
              <p className="mt-1.5 text-sm font-semibold text-error">The two passwords don't match</p>
            )}
          </div>
          {error && (
            <p className="text-sm font-semibold text-error">
              {error}{" "}
              <Link to="/forgot-password" className="link link-secondary">
                Request a fresh one
              </Link>
            </p>
          )}
          <button type="submit" className="btn btn-primary btn-lg w-full" disabled={busy || mismatch}>
            {busy ? <span className="loading loading-spinner loading-sm" /> : "Set new password"}
          </button>
        </form>
      )}

      <p className="dash-divider mt-8 pt-6 text-sm">
        <Link to="/login" className="link link-secondary font-semibold">
          Log in
        </Link>
      </p>
    </AuthLayout>
  );
}
