import { MessageSquare } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router";
import client, { apiError } from "../api/client";
import { returnPath } from "../lib/navigation";
import { identify, looksLikePhone } from "../lib/signin";
import { fetchStore } from "../lib/store";
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
  const { login, phoneLogin, user, ready } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();

  // One field for either way in: an email address or a phone number.
  const [who, setWho] = useState("");
  const [password, setPassword] = useState("");
  const [unclear, setUnclear] = useState(false);
  const [busy, setBusy] = useState(false);
  const [demoAccounts, setDemoAccounts] = useState(false);
  // Which extra sign-in rails this deployment offers — a rail with no keys
  // behind it never shows a customer a button that can't work.
  const [rails, setRails] = useState({ phone: false, facebook: false });

  // The texted-code rail (only where the shop can send texts): the number,
  // the "code is out" step, the code itself.
  const [byCode, setByCode] = useState(false);
  const [phone, setPhone] = useState("");
  const [codeSent, setCodeSent] = useState(null); // { message, dev_code? }
  const [code, setCode] = useState("");
  const [phoneBusy, setPhoneBusy] = useState(false);
  const [fbBusy, setFbBusy] = useState(false);

  // Demo logins only exist where demo data was seeded — never on a real shop.
  useEffect(() => {
    fetchStore()
      .then((data) => {
        setDemoAccounts(Boolean(data.demo_accounts));
        setRails({ phone: Boolean(data.phone_login), facebook: Boolean(data.facebook_login) });
      })
      .catch(() => {});
  }, []);

  const from = returnPath(location);

  // Already signed in (a bookmark, the back button, a second tab that just
  // logged in): there is nothing to do here — on to where they were going.
  if (ready && user && !busy && !phoneBusy) return <Navigate to={from} replace />;

  const submit = async (e) => {
    e.preventDefault();
    const id = identify(who);
    if (!id) {
      setUnclear(true);
      return;
    }
    setBusy(true);
    try {
      const user = await login(id, password);
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

  // Over to the texted code, carrying across a number already typed.
  const useCode = () => {
    if (looksLikePhone(who)) setPhone(who.trim());
    setByCode(true);
  };

  const usePassword = () => {
    setByCode(false);
    setCodeSent(null);
    setCode("");
  };

  return (
    <div className="mx-auto flex max-w-md flex-col px-4 py-16">
      <div className="rise-in rounded-box glass p-8 shadow-lg ring-1 ring-base-300/60">
        <img src="/static/site/logo.png" alt="" className="mx-auto h-14 w-14 object-contain" />
        <h1 className="font-display mt-4 text-center text-3xl font-semibold">Welcome back</h1>
        <p className="mt-1 text-center text-sm opacity-70">Log in to check out faster.</p>

        {!byCode ? (
          <form onSubmit={submit} className="mt-6 space-y-4">
            <div>
              <label className="label text-xs" htmlFor="email">Email or phone number</label>
              {/* One field, either way in. The email keyboard has the letters,
                  the digits, "@" and "+" — enough for both. */}
              <input
                id="email"
                type="text"
                inputMode="email"
                className={`input input-bordered w-full ${unclear ? "input-error" : ""}`}
                placeholder="you@example.com or 0176 555 0100"
                autoComplete="username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                required
                aria-invalid={unclear}
                aria-describedby={unclear ? "login-who-problem" : undefined}
                value={who}
                onChange={(e) => {
                  setWho(e.target.value);
                  setUnclear(false);
                }}
              />
              {unclear && (
                <p id="login-who-problem" className="mt-1.5 text-xs font-semibold text-error" role="alert">
                  Enter the email address or the phone number on your account.
                </p>
              )}
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
                placeholder="0176 555 0100"
                required
                maxLength={30}
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
              <p className="mt-1.5 text-xs opacity-70">
                The number on your account. From outside Germany, start with + and your country code.
              </p>
            </div>
            <button type="submit" className="btn btn-primary w-full" disabled={phoneBusy || !phone}>
              {phoneBusy ? (
                <span className="loading loading-spinner loading-sm" />
              ) : (
                <MessageSquare size={16} aria-hidden="true" />
              )}
              {phoneBusy ? "Sending…" : "Text me a sign-in code"}
            </button>
          </form>
        )}

        {/* The other ways in. A texted code only where the shop can send
            texts; Facebook only where its keys are set. */}
        {(rails.phone || rails.facebook) && (
          <div className="mt-5 flex items-center gap-3 text-xs opacity-70">
            <span className="h-px flex-1 bg-base-content/20" />
            or
            <span className="h-px flex-1 bg-base-content/20" />
          </div>
        )}
        {rails.phone &&
          (byCode ? (
            <button type="button" onClick={usePassword} className="btn btn-ghost btn-sm mt-3 w-full border border-base-300">
              Log in with a password instead
            </button>
          ) : (
            <button type="button" onClick={useCode} className="btn btn-ghost btn-sm mt-3 w-full border border-base-300">
              <MessageSquare size={15} aria-hidden="true" />
              Text me a sign-in code
            </button>
          ))}
        {rails.facebook && (
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
          <p className="text-center text-xs font-bold tracking-widest uppercase opacity-65">
            Demo accounts
          </p>
          <div className="mt-3 grid grid-cols-2 gap-2">
            {DEMO_ACCOUNTS.map((acct) => (
              <button
                key={acct.email}
                type="button"
                className="btn btn-ghost btn-xs border border-base-300"
                onClick={() => {
                  usePassword();
                  setWho(acct.email);
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
