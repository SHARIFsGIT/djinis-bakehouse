import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router";
import client, { apiError } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";

const DEMO_ACCOUNTS = [
  { label: "Customer demo", email: "customer@example.com", password: "Customer123!" },
  { label: "Admin demo", email: "admin@djinisbakehouse.com", password: "Admin123!" },
];

export default function Login() {
  const { login } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [demoAccounts, setDemoAccounts] = useState(false);

  // Demo logins only exist where demo data was seeded — never on a real shop.
  useEffect(() => {
    client
      .get("/store")
      .then(({ data }) => setDemoAccounts(Boolean(data.demo_accounts)))
      .catch(() => {});
  }, []);

  const from = params.get("from") || location.state?.from || "/";

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const user = await login(email, password);
      toast.success(`Welcome back, ${user.first_name}`);
      navigate(from, { replace: true });
    } catch (err) {
      toast.error(apiError(err, "Couldn't log in"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-md flex-col px-4 py-16">
      <div className="rise-in rounded-box glass p-8 shadow-lg ring-1 ring-base-300/60">
        <img src="/static/site/logo.png" alt="" className="mx-auto h-14 w-14 object-contain" />
        <h1 className="font-display mt-4 text-center text-3xl font-semibold">Welcome back</h1>
        <p className="mt-1 text-center text-sm opacity-70">Log in to check out faster.</p>

        <form onSubmit={submit} className="mt-6 space-y-4">
          <div>
            <label className="label text-xs" htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              className="input input-bordered w-full"
              placeholder="you@example.com"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <label className="label text-xs" htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              className="input input-bordered w-full"
              placeholder="••••••••"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <button type="submit" className="btn btn-primary btn w-full" disabled={busy}>
            {busy ? <span className="loading loading-spinner loading-sm" /> : "Log in"}
          </button>
        </form>

        <p className="mt-4 text-center text-sm opacity-70">
          New here?{" "}
          <Link to="/register" state={{ from }} className="link link-secondary font-semibold">
            Create an account
          </Link>
        </p>
      </div>

      {/* Demo credentials — handy for exploring, hidden on real deployments */}
      {demoAccounts && (
        <div className="mt-6 rounded-box border border-dashed border-base-300 p-4">
          <p className="text-center text-xs font-bold tracking-widest uppercase opacity-50">
            Demo accounts
          </p>
          <div className="mt-3 grid grid-cols-2 gap-2">
            {DEMO_ACCOUNTS.map((acct) => (
              <button
                key={acct.email}
                type="button"
                className="btn btn-ghost btn-xs border border-base-300"
                onClick={() => {
                  setEmail(acct.email);
                  setPassword(acct.password);
                }}
              >
                {acct.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
