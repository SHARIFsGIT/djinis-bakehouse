import { Mail, MessageSquare, Smartphone } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import client, { apiError } from "../api/client";
import PasswordInput from "../components/PasswordInput";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";

/** The Facebook "f" — lucide dropped its brand icons, so the one mark that
 *  must be recognizable is drawn here (viewBox 24, currentColor). */
function FacebookMark({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M13.5 21v-7h2.4l.4-3h-2.8V9.1c0-.9.3-1.5 1.6-1.5h1.3V4.9c-.3 0-1.1-.1-2.1-.1-2.1 0-3.6 1.3-3.6 3.7V11H8.2v3h2.5v7h2.8z" />
    </svg>
  );
}

const DEMO_ACCOUNTS = [
  { label: "Customer demo", email: "customer@example.com", password: "Customer123!" },
  { label: "Admin demo", email: "admin@djinisbakehouse.com", password: "Admin123!" },
];

export default function Login() {
  const { login, phoneLogin } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [demoAccounts, setDemoAccounts] = useState(false);
  // Which sign-in rails this deployment offers — a rail with no keys behind
  // it never shows a customer a button that can't work.
  const [rails, setRails] = useState({ phone: false, facebook: false });
  const [tab, setTab] = useState("email"); // "email" | "phone"

  // Phone sign-in: the number, the "code is out" step, the code itself.
  const [phone, setPhone] = useState("");
  const [codeSent, setCodeSent] = useState(null); // { message, dev_code? }
  const [code, setCode] = useState("");
  const [phoneBusy, setPhoneBusy] = useState(false);
  const [fbBusy, setFbBusy] = useState(false);

  // Demo logins only exist where demo data was seeded — never on a real shop.
  useEffect(() => {
    client
      .get("/store")
      .then(({ data }) => {
        setDemoAccounts(Boolean(data.demo_accounts));
        setRails({ phone: Boolean(data.phone_login), facebook: Boolean(data.facebook_login) });
      })
      .catch(() => {});
  }, []);

  const from = location.state?.from || "/";

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

  const sendCode = async (e) => {
    e.preventDefault();
    if (phoneBusy || !phone) return;
    setPhoneBusy(true);
    try {
      const { data } = await client.post("/auth/phone/request", { phone });
      setCodeSent(data);
    } catch (err) {
      toast.error(apiError(err, "Couldn't send the code"));
    } finally {
      setPhoneBusy(false);
    }
  };

  const verifyCode = async (e) => {
    e.preventDefault();
    if (phoneBusy || code.length !== 6) return;
    setPhoneBusy(true);
    try {
      const user = await phoneLogin(phone, code);
      toast.success(`Welcome back, ${user.first_name}`);
      navigate(from, { replace: true });
    } catch (err) {
      toast.error(apiError(err, "Couldn't verify the code"));
    } finally {
      setPhoneBusy(false);
    }
  };

  const goFacebook = async () => {
    if (fbBusy) return;
    setFbBusy(true);
    try {
      const { data } = await client.get("/auth/facebook/start");
      window.location.href = data.url; // off to Facebook's dialog — it brings the browser back
    } catch (err) {
      toast.error(apiError(err, "Facebook sign-in isn't available"));
      setFbBusy(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-md flex-col px-4 py-16">
      <div className="rise-in rounded-box glass p-8 shadow-lg ring-1 ring-base-300/60">
        <img src="/static/site/logo.png" alt="" className="mx-auto h-14 w-14 object-contain" />
        <h1 className="font-display mt-4 text-center text-3xl font-semibold">Welcome back</h1>
        <p className="mt-1 text-center text-sm opacity-70">Log in to check out faster.</p>

        {rails.phone && (
          <div className="mt-6 grid grid-cols-2 gap-1 rounded-full bg-base-200 p-1" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "email"}
              onClick={() => setTab("email")}
              className={`btn btn-sm rounded-full ${tab === "email" ? "btn-secondary" : "btn-ghost"}`}
            >
              <Mail size={15} />
              Email
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "phone"}
              onClick={() => setTab("phone")}
              className={`btn btn-sm rounded-full ${tab === "phone" ? "btn-secondary" : "btn-ghost"}`}
            >
              <Smartphone size={15} />
              Phone
            </button>
          </div>
        )}

        {tab === "email" ? (
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
              <div className="mb-1 flex items-baseline justify-between gap-4">
                <label className="label text-xs" htmlFor="password">Password</label>
                <Link to="/forgot-password" className="text-xs link link-secondary">Forgot?</Link>
              </div>
              <PasswordInput
                id="password"
                placeholder="••••••••"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            <button type="submit" className="btn btn-primary w-full" disabled={busy}>
              {busy ? <span className="loading loading-spinner loading-sm" /> : "Log in"}
            </button>
          </form>
        ) : codeSent ? (
          <div className="mt-6 space-y-4">
            <div className="rounded-box border border-success/40 bg-success/10 p-4 text-sm">
              <p className="font-semibold">{codeSent.message}</p>
              {codeSent.dev_code && (
                <p className="mt-2 font-mono">
                  Dev shortcut (not sent in production): code {codeSent.dev_code}
                </p>
              )}
            </div>
            <form onSubmit={verifyCode} className="space-y-4">
              <div>
                <label className="label text-xs" htmlFor="sms-code">Sign-in code</label>
                <input
                  id="sms-code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  className="input input-bordered w-full text-center font-mono tracking-[0.4em]"
                  placeholder="······"
                  maxLength={6}
                  required
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                />
              </div>
              <button type="submit" className="btn btn-primary w-full" disabled={phoneBusy || code.length !== 6}>
                {phoneBusy ? <span className="loading loading-spinner loading-sm" /> : "Verify & sign in"}
              </button>
            </form>
            <p className="text-center text-sm opacity-70">
              Wrong number, or nothing arrived?{" "}
              <button
                type="button"
                className="link link-secondary font-semibold"
                onClick={() => {
                  setCodeSent(null);
                  setCode("");
                }}
              >
                Use a different number
              </button>
            </p>
          </div>
        ) : (
          <form onSubmit={sendCode} className="mt-6 space-y-4">
            <div>
              <label className="label text-xs" htmlFor="login-phone">Phone number</label>
              <input
                id="login-phone"
                type="tel"
                autoComplete="tel"
                className="input input-bordered w-full"
                placeholder="+49 176 555 0100"
                required
                maxLength={30}
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
              <p className="mt-1.5 text-xs opacity-60">
                The number saved on your account — country code included.
              </p>
            </div>
            <button type="submit" className="btn btn-primary w-full" disabled={phoneBusy || !phone}>
              {phoneBusy ? (
                <span className="loading loading-spinner loading-sm" />
              ) : (
                <MessageSquare size={16} />
              )}
              {phoneBusy ? "Sending…" : "Text me a sign-in code"}
            </button>
          </form>
        )}

        {rails.facebook && (
          <>
            <div className="mt-5 flex items-center gap-3 text-xs opacity-50">
              <span className="h-px flex-1 bg-base-content/20" />
              or
              <span className="h-px flex-1 bg-base-content/20" />
            </div>
            <button
              type="button"
              onClick={goFacebook}
              disabled={fbBusy}
              className="btn btn-sm mt-3 w-full border-none text-white hover:border-none"
              style={{ backgroundColor: "#1877F2" }}
            >
              {fbBusy ? <span className="loading loading-spinner loading-xs" /> : <FacebookMark size={16} />}
              Continue with Facebook
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
