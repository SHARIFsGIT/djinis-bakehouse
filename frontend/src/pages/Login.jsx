import { Eye, EyeOff, Mail } from "lucide-react";
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
  const { login, loginWithToken } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [demoAccounts, setDemoAccounts] = useState(false);
  // The one-tap flow: asking for a link, the "it's on its way" panel, and
  // what to say when an arrived link no longer works.
  const [linkBusy, setLinkBusy] = useState(false);
  const [linkSent, setLinkSent] = useState(null); // { message, login_link? }
  const [redeemError, setRedeemError] = useState("");

  // Demo logins only exist where demo data was seeded — never on a real shop.
  useEffect(() => {
    client
      .get("/store")
      .then(({ data }) => setDemoAccounts(Boolean(data.demo_accounts)))
      .catch(() => {});
  }, []);

  const from = params.get("from") || location.state?.from || "/";
  const token = params.get("token");

  // Arriving from an emailed sign-in link: redeem it and land signed in.
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    loginWithToken(token)
      .then((user) => {
        if (cancelled) return;
        toast.success(`Welcome back, ${user.first_name}`);
        navigate(from, { replace: true });
      })
      .catch(() => {
        if (!cancelled)
          setRedeemError(
            "That sign-in link has expired or was already used — send yourself a fresh one below.",
          );
      });
    return () => {
      cancelled = true;
    };
    // Runs once per arrival; `token` in the URL is what changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  const sendLink = async () => {
    if (!email || linkBusy) return;
    setLinkBusy(true);
    try {
      const { data } = await client.post("/auth/quick-login", { email });
      setLinkSent(data);
    } catch (err) {
      toast.error(apiError(err, "Couldn't send the link"));
    } finally {
      setLinkBusy(false);
    }
  };

  const redeeming = Boolean(token) && !redeemError;

  return (
    <div className="mx-auto flex max-w-md flex-col px-4 py-16">
      <div className="rise-in rounded-box glass p-8 shadow-lg ring-1 ring-base-300/60">
        <img src="/static/site/logo.png" alt="" className="mx-auto h-14 w-14 object-contain" />
        <h1 className="font-display mt-4 text-center text-3xl font-semibold">Welcome back</h1>
        <p className="mt-1 text-center text-sm opacity-70">Log in to check out faster.</p>

        {redeeming ? (
          <div className="mt-8 flex flex-col items-center gap-3 py-8">
            <span className="loading loading-spinner" />
            <p className="text-sm opacity-70">Opening your sign-in link…</p>
          </div>
        ) : linkSent ? (
          <div className="mt-6 space-y-4">
            <div className="rounded-box border border-success/40 bg-success/10 p-4 text-sm">
              <p className="font-semibold">{linkSent.message}</p>
              {linkSent.login_link && (
                <p className="mt-2 break-all">
                  Dev shortcut (not sent in production):{" "}
                  <a href={linkSent.login_link} className="link link-secondary">
                    open the sign-in link
                  </a>
                </p>
              )}
            </div>
            <p className="text-center text-sm opacity-70">
              Remembered the password?{" "}
              <button
                type="button"
                className="link link-secondary font-semibold"
                onClick={() => setLinkSent(null)}
              >
                Log in instead
              </button>
            </p>
          </div>
        ) : (
          <>
            {redeemError && (
              <div className="alert alert-warning mt-6 items-start text-sm">{redeemError}</div>
            )}

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
                <div className="flex items-baseline justify-between">
                  <label className="label text-xs" htmlFor="password">Password</label>
                  <Link to="/forgot-password" className="text-xs link link-secondary">Forgot?</Link>
                </div>
                <div className="relative">
                  <input
                    id="password"
                    type={showPassword ? "text" : "password"}
                    className="input input-bordered w-full pr-11"
                    placeholder="••••••••"
                    autoComplete="current-password"
                    required
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
              <button type="submit" className="btn btn-primary btn w-full" disabled={busy}>
                {busy ? <span className="loading loading-spinner loading-sm" /> : "Log in"}
              </button>
            </form>

            <div className="mt-5 flex items-center gap-3 text-xs opacity-50">
              <span className="h-px flex-1 bg-base-content/20" />
              or
              <span className="h-px flex-1 bg-base-content/20" />
            </div>

            <button
              type="button"
              className="btn btn-ghost btn-sm mt-3 w-full border border-base-300"
              onClick={sendLink}
              disabled={linkBusy || !email}
              title={email ? undefined : "Type your email first"}
            >
              {linkBusy ? <span className="loading loading-spinner loading-xs" /> : <Mail size={16} />}
              {linkBusy ? "Sending…" : "Email me a one-tap sign-in link"}
            </button>
          </>
        )}

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
