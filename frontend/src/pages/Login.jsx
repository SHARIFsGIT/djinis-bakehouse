import { MessageSquare } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router";
import client, { apiError } from "../api/client";
import { returnPath } from "../lib/navigation";
import { identifyAs, initialWay, rememberWay, wayProblem } from "../lib/signin";
import { fetchStore } from "../lib/store";
import PasswordInput from "../components/PasswordInput";
import SignInWay from "../components/SignInWay";
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

  // Two tabs, two ways in: an email address or a phone number. Each keeps
  // its own typing, so a switch back finds it where it was left.
  const [way, setWay] = useState(() => initialWay(location.search));
  const [switched, setSwitched] = useState(false); // the panel slides only on a real switch, not on arrival
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState(""); // the Phone tab's number, for a password or a texted code
  const [password, setPassword] = useState("");
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const [demoAccounts, setDemoAccounts] = useState(false);
  // Which extra sign-in rails this deployment offers — a rail with no keys
  // behind it never shows a customer a button that can't work.
  const [rails, setRails] = useState({ phone: false, facebook: false });

  // The texted-code rail, inside the Phone tab (only where the shop can
  // send texts): the "code is out" step, the code itself.
  const [byCode, setByCode] = useState(false);
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

  const backToPassword = () => {
    setByCode(false);
    setCodeSent(null);
    setCode("");
  };

  const switchWay = (next) => {
    if (next !== way) setSwitched(true);
    setWay(next);
    setProblem("");
    backToPassword();
  };

  const submit = async (e) => {
    e.preventDefault();
    const typed = way === "email" ? email : phone;
    const id = identifyAs(way, typed);
    if (!id) {
      setProblem(wayProblem(way, typed));
      return;
    }
    setBusy(true);
    try {
      const user = await login(id, password);
      rememberWay(way);
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
      rememberWay("phone");
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

  const field = (id, label, props) => (
    <div>
      <label className="label text-xs" htmlFor={id}>{label}</label>
      <input
        id={id}
        className={`input input-bordered w-full ${problem ? "input-error" : ""}`}
        autoCapitalize="none"
        autoCorrect="off"
        spellCheck={false}
        required
        aria-invalid={Boolean(problem)}
        aria-describedby={problem ? "login-who-problem" : undefined}
        {...props}
        onChange={(e) => {
          props.onChange(e);
          setProblem("");
        }}
      />
      {problem && (
        <p id="login-who-problem" className="mt-1.5 text-xs font-semibold text-error" role="alert">
          {problem}
        </p>
      )}
    </div>
  );

  return (
    <div className="mx-auto flex max-w-md flex-col px-4 py-16">
      {/* On a phone under 390px the card gives its sides 20px instead of 32,
          so the two tabs and the fields keep their room. */}
      <div className="rise-in rounded-box glass px-5 py-8 shadow-lg ring-1 ring-base-300/60 min-[390px]:px-8">
        <img src="/static/site/logo.png" alt="" className="mx-auto h-14 w-14 object-contain" />
        <h1 className="font-display mt-4 text-center text-3xl font-semibold">Welcome back</h1>
        <p className="mt-1 text-center text-sm opacity-70">Log in to check out faster.</p>

        <div className="mt-6">
          <SignInWay value={way} onChange={switchWay} idPrefix="login" label="Log in with" />
        </div>

        <div
          key={way}
          id="login-panel"
          role="tabpanel"
          aria-labelledby={`login-tab-${way}`}
          className={`mt-5 ${switched ? (way === "phone" ? "slide-from-right" : "slide-from-left") : ""}`}
        >
          {way === "phone" && byCode ? (
            codeSent ? (
              <div className="space-y-4">
                <div className="rounded-box border border-success/40 bg-success/10 p-4 text-sm">
                  <p className="font-semibold">{codeSent.message}</p>
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
              <form onSubmit={sendCode} className="space-y-4">
                <div>
                  <label className="label text-xs" htmlFor="login-phone">Phone number</label>
                  <input
                    id="login-phone"
                    type="tel"
                    autoComplete="tel"
                    className="input input-bordered w-full"
                    required
                    maxLength={30}
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                  />
                  <p className="mt-1.5 text-xs opacity-70">
                    The mobile number on your account. Text sign-in works once you&rsquo;ve
                    confirmed the number in your profile.
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
            )
          ) : (
            <form onSubmit={submit} className="space-y-4">
              {way === "email"
                ? field("email", "Email", {
                    type: "text",
                    inputMode: "email",
                    autoComplete: "username",
                    value: email,
                    onChange: (e) => setEmail(e.target.value),
                  })
                : field("login-phone", "Phone number", {
                    type: "tel",
                    autoComplete: "username",
                    maxLength: 30,
                    value: phone,
                    onChange: (e) => setPhone(e.target.value),
                  })}
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
          )}

          {/* A texted code instead of the password — only where the shop
              can send texts, and only on the Phone tab, where it belongs. */}
          {way === "phone" &&
            rails.phone &&
            (byCode ? (
              <button type="button" onClick={backToPassword} className="btn btn-ghost btn-sm mt-3 w-full border border-base-300">
                Use my password instead
              </button>
            ) : (
              <button
                type="button"
                onClick={() => setByCode(true)}
                className="btn btn-ghost btn-sm mt-3 w-full border border-base-300"
              >
                <MessageSquare size={15} aria-hidden="true" />
                Text me a sign-in code instead
              </button>
            ))}
        </div>

        {/* Facebook is a third way in, whichever tab is open — only where
            its keys are set. */}
        {rails.facebook && (
          <>
            <div className="mt-5 flex items-center gap-3 text-xs opacity-70">
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
          <Link to={`/register?with=${way}`} state={{ from }} className="link link-secondary font-semibold">
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
                  switchWay("email");
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
