import { MessageSquare } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router";
import client, { apiError } from "../api/client";
import { returnPath } from "../lib/navigation";
import { identify, looksLikePhone } from "../lib/signin";
import { fetchStore } from "../lib/store";
import AuthLayout from "../components/AuthLayout";
import PasswordInput from "../components/PasswordInput";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";

/** Facebook's round mark, in Facebook's own blue — lucide dropped its brand
 *  icons, so the one mark that must be recognizable is drawn here. The "f"
 *  is cut out of the disc; the white disc behind it keeps it white at night
 *  too, whatever the button underneath. */
function FacebookMark({ size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="11.5" fill="#fff" />
      <path
        fill="#1877F2"
        d="M9.101 23.691v-7.98H6.627v-3.667h2.474v-1.58c0-4.085 1.848-5.978 5.858-5.978.401 0 .955.042 1.468.103a8.68 8.68 0 0 1 1.141.195v3.325a8.623 8.623 0 0 0-.653-.036 26.805 26.805 0 0 0-.733-.009c-.707 0-1.259.096-1.675.309a1.686 1.686 0 0 0-.679.622c-.258.42-.374.995-.374 1.752v1.297h3.919l-.386 2.103-.287 1.564h-3.246v8.245C19.396 23.238 24 18.179 24 12.044c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.628 3.874 10.35 9.101 11.647Z"
      />
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
  const [codeSent, setCodeSent] = useState(null); // { message } — never the code itself
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
    <AuthLayout>
      <h1 className="font-display text-4xl leading-[1.05] font-semibold text-balance sm:text-5xl">
        Welcome <em className="text-secondary italic">back</em>
      </h1>
      <p className="mt-3 text-lg leading-relaxed text-pretty opacity-80">Log in to check out faster.</p>

      {!byCode ? (
        <form onSubmit={submit} className="mt-8 space-y-5">
          <div>
            <label className="label text-sm" htmlFor="email">Email or phone number</label>
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
              <p id="login-who-problem" className="mt-1.5 text-sm font-semibold text-error" role="alert">
                Enter the email address or the phone number on your account.
              </p>
            )}
          </div>
          <div>
            <div className="flex items-baseline justify-between gap-4">
              <label className="label text-sm" htmlFor="password">Password</label>
              <Link to="/forgot-password" className="link link-secondary text-sm">Forgot?</Link>
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
          <button type="submit" className="btn btn-primary btn-lg w-full" disabled={busy}>
            {busy ? <span className="loading loading-spinner loading-sm" /> : "Log in"}
          </button>
        </form>
      ) : codeSent ? (
        <div className="mt-8 space-y-5">
          <div className="rounded-box border border-success/40 bg-success/10 p-4 text-sm">
            <p className="font-semibold">{codeSent.message}</p>
          </div>
          <form onSubmit={verifyCode} className="space-y-5">
            <div>
              <label className="label text-sm" htmlFor="sms-code">Sign-in code</label>
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
            <button type="submit" className="btn btn-primary btn-lg w-full" disabled={phoneBusy || code.length !== 6}>
              {phoneBusy ? <span className="loading loading-spinner loading-sm" /> : "Verify & sign in"}
            </button>
          </form>
          <p className="text-sm">
            <span className="opacity-75">Wrong number, or nothing arrived?</span>{" "}
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
        <form onSubmit={sendCode} className="mt-8 space-y-5">
          <div>
            <label className="label text-sm" htmlFor="login-phone">Phone number</label>
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
            <p className="mt-1.5 text-sm opacity-75">
              The mobile number on your account. Text sign-in works once you&rsquo;ve
              confirmed the number in your profile.
            </p>
          </div>
          <button type="submit" className="btn btn-primary btn-lg w-full" disabled={phoneBusy || !phone}>
            {phoneBusy ? (
              <span className="loading loading-spinner loading-sm" />
            ) : (
              <MessageSquare size={18} aria-hidden="true" />
            )}
            {phoneBusy ? "Sending…" : "Text me a sign-in code"}
          </button>
        </form>
      )}

      {/* The other ways in. A texted code only where the shop can send
          texts; Facebook only where its keys are set. Both drawn the same
          quiet way — outlined, the wine button above stays the one to
          press — with Facebook's blue kept to its own mark. */}
      {(rails.phone || rails.facebook) && (
        <div className="mt-6 space-y-3">
          <div className="flex items-center gap-3 text-sm opacity-70">
            <span className="h-px flex-1 bg-base-content/20" />
            or
            <span className="h-px flex-1 bg-base-content/20" />
          </div>
          {rails.phone &&
            (byCode ? (
              <button type="button" onClick={usePassword} className="btn btn-ghost btn-lg w-full border border-base-300 text-base transition hover:bg-base-200">
                Log in with a password instead
              </button>
            ) : (
              <button type="button" onClick={useCode} className="btn btn-ghost btn-lg w-full border border-base-300 text-base transition hover:bg-base-200">
                <MessageSquare size={17} aria-hidden="true" />
                Text me a sign-in code
              </button>
            ))}
          {rails.facebook && (
            <button
              type="button"
              onClick={goFacebook}
              disabled={fbBusy}
              className="btn btn-ghost btn-lg w-full border border-base-300 text-base transition hover:bg-base-200"
            >
              {fbBusy ? <span className="loading loading-spinner loading-sm" /> : <FacebookMark size={20} />}
              Continue with Facebook
            </button>
          )}
        </div>
      )}

      <p className="dash-divider mt-8 pt-6 text-sm">
        <span className="opacity-75">New here?</span>{" "}
        <Link to="/register" state={{ from }} className="link link-secondary font-semibold">
          Create an account
        </Link>
      </p>

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
    </AuthLayout>
  );
}
