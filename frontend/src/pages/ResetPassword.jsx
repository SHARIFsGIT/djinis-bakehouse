import { Eye, EyeOff } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import client, { apiError } from "../api/client";
import { useToast } from "../context/ToastContext";

export default function ResetPassword() {
  const toast = useToast();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const token = params.get("token") || "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
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
    <div className="mx-auto flex max-w-md flex-col px-4 py-16">
      <div className="rise-in rounded-box glass p-8 shadow-lg ring-1 ring-base-300/60">
        <img src="/static/site/logo.png" alt="" className="mx-auto h-14 w-14 object-contain" />
        <h1 className="font-display mt-4 text-center text-3xl font-semibold">Reset your password</h1>
        <p className="mt-1 text-center text-sm opacity-70">Pick something fresh — eight characters or more.</p>

        {!token ? (
          <div className="mt-6 space-y-4">
            <div className="rounded-box border border-error/40 bg-error/10 p-4 text-sm">
              <p className="font-semibold">This reset link is missing its key.</p>
              <p className="mt-1">
                Start again from{" "}
                <Link to="/forgot-password" className="link link-secondary font-semibold">
                  forgot password
                </Link>{" "}
                and use the link exactly as emailed.
              </p>
            </div>
          </div>
        ) : (
          <form onSubmit={submit} className="mt-6 space-y-4">
            <div>
              <label className="label text-xs" htmlFor="new-password">New password</label>
              <div className="relative">
                <input
                  id="new-password"
                  type={showPassword ? "text" : "password"}
                  className="input input-bordered w-full pr-11"
                  placeholder="At least 8 characters"
                  autoComplete="new-password"
                  required
                  minLength={8}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  className="btn btn-ghost btn-xs btn-square absolute top-1/2 right-1.5 -translate-y-1/2"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>
            <div>
              <label className="label text-xs" htmlFor="confirm-password">Confirm password</label>
              <input
                id="confirm-password"
                type={showPassword ? "text" : "password"}
                className={`input input-bordered w-full ${mismatch ? "input-error" : ""}`}
                placeholder="Once more"
                autoComplete="new-password"
                required
                minLength={8}
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
              />
              {mismatch && <p className="mt-1 text-xs text-error">The two passwords don't match</p>}
            </div>
            {error && (
              <p className="text-sm text-error">
                {error}{" "}
                <Link to="/forgot-password" className="link link-secondary font-semibold">
                  Request a fresh one
                </Link>
              </p>
            )}
            <button type="submit" className="btn btn-primary btn w-full" disabled={busy || mismatch}>
              {busy ? <span className="loading loading-spinner loading-sm" /> : "Set new password"}
            </button>
          </form>
        )}

        <p className="mt-4 text-center text-sm opacity-70">
          <Link to="/login" className="link link-secondary font-semibold">
            Log in
          </Link>
        </p>
      </div>
    </div>
  );
}
