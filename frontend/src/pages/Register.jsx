import { useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router";
import { apiError } from "../api/client";
import PasswordInput from "../components/PasswordInput";
import SignInWay from "../components/SignInWay";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { returnPath } from "../lib/navigation";
import { identifyAs, initialWay, rememberWay, saysItsCountry, wayProblem } from "../lib/signin";

const PHONE_NOTE =
  "You'll log in with this number. Receipts and password resets go by email — add one in your profile whenever you like.";

export default function Register() {
  const { register, user, ready } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();

  // Three things to type: a name, one way in — an email address or a phone
  // number, each on its own tab — and a password. Each tab keeps its own
  // typing, so a switch back finds it where it was left.
  const [way, setWay] = useState(() => initialWay(location.search));
  const [switched, setSwitched] = useState(false); // the panel slides only on a real switch, not on arrival
  const [form, setForm] = useState({ name: "", email: "", phone: "", password: "" });
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);

  const from = returnPath(location);

  const set = (key) => (e) => {
    setForm((f) => ({ ...f, [key]: e.target.value }));
    if (key === way) setProblem("");
  };

  // Already signed in — nothing to create.
  if (ready && user && !busy) return <Navigate to={from} replace />;

  const switchWay = (next) => {
    if (next !== way) setSwitched(true);
    setWay(next);
    setProblem("");
  };

  const submit = async (e) => {
    e.preventDefault();
    // One "Your name" field: the first word is the first name, whatever
    // follows is the last — a single name is fine too.
    const parts = form.name.trim().split(/\s+/);
    if (!parts[0]) return;
    const typed = form[way];
    const id = identifyAs(way, typed);
    if (!id) {
      setProblem(wayProblem(way, typed, { signingUp: true }));
      return;
    }
    if (id.phone && !saysItsCountry(id.phone)) {
      setProblem("Start the number with 0, or with + and your country code (like +49 176 555 0100).");
      return;
    }
    setBusy(true);
    try {
      const user = await register({
        first_name: parts[0],
        last_name: parts.slice(1).join(" "),
        password: form.password,
        ...id,
      });
      rememberWay(way);
      toast.success(`Welcome to the bakehouse, ${user.first_name}`);
      navigate(from, { replace: true });
    } catch (err) {
      toast.error(apiError(err, "Couldn't create the account"));
    } finally {
      setBusy(false);
    }
  };

  // The way-in field, as its tab wants it: an address under Email, a
  // number under Phone (with what a number alone means for receipts).
  const wayField =
    way === "email" ? (
      <div>
        <label className="label text-xs" htmlFor="reg-email">Email</label>
        <input
          id="reg-email"
          type="text"
          inputMode="email"
          className={`input input-bordered w-full ${problem ? "input-error" : ""}`}
          autoComplete="username"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          required
          maxLength={255}
          aria-invalid={Boolean(problem)}
          aria-describedby={problem ? "reg-who-note" : undefined}
          value={form.email}
          onChange={set("email")}
        />
        {problem && (
          <p id="reg-who-note" className="mt-1.5 text-xs font-semibold text-error" role="alert">
            {problem}
          </p>
        )}
      </div>
    ) : (
      <div>
        <label className="label text-xs" htmlFor="reg-phone">Phone number</label>
        <input
          id="reg-phone"
          type="tel"
          className={`input input-bordered w-full ${problem ? "input-error" : ""}`}
          autoComplete="tel"
          required
          maxLength={30}
          aria-invalid={Boolean(problem)}
          aria-describedby="reg-who-note"
          value={form.phone}
          onChange={set("phone")}
        />
        <p
          id="reg-who-note"
          className={`mt-1.5 text-xs ${problem ? "font-semibold text-error" : "opacity-70"}`}
          role={problem ? "alert" : undefined}
        >
          {problem || PHONE_NOTE}
        </p>
      </div>
    );

  return (
    <div className="mx-auto max-w-md px-4 py-16">
      {/* As on the log-in page: under 390px the card gives its sides 20px
          instead of 32, so the two tabs and the fields keep their room. */}
      <div className="rise-in rounded-box glass px-5 py-8 shadow-lg ring-1 ring-base-300/60 min-[390px]:px-8">
        <img src="/static/site/logo.png" alt="" className="mx-auto h-14 w-14 object-contain" />
        <h1 className="font-display mt-4 text-center text-3xl font-semibold">Join the bakehouse</h1>
        <p className="mt-1 text-center text-sm opacity-70">
          Faster checkout, order history, first dibs on seasonal bakes.
        </p>

        <div className="mt-6">
          <SignInWay value={way} onChange={switchWay} idPrefix="register" label="Sign up with" />
        </div>

        <div
          key={way}
          id="register-panel"
          role="tabpanel"
          aria-labelledby={`register-tab-${way}`}
          className={`mt-5 ${switched ? (way === "phone" ? "slide-from-right" : "slide-from-left") : ""}`}
        >
          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="label text-xs" htmlFor="name">Your name</label>
              <input
                id="name"
                className="input input-bordered w-full"
                autoComplete="name"
                required
                maxLength={200}
                value={form.name}
                onChange={set("name")}
              />
            </div>
            {wayField}
            <PasswordInput
              id="reg-password"
              label="Password"
              placeholder="At least 8 characters"
              autoComplete="new-password"
              required
              minLength={8}
              value={form.password}
              onChange={set("password")}
            />
            <button type="submit" className="btn btn-primary w-full" disabled={busy}>
              {busy ? <span className="loading loading-spinner loading-sm" /> : "Create account"}
            </button>
            {/* What signing up means, where it is decided — with the two
                documents one tap away. */}
            <p className="text-center text-xs opacity-70">
              By creating an account you accept our{" "}
              <Link to="/terms" className="link">terms</Link>. How we handle your details
              is in the <Link to="/privacy" className="link">privacy policy</Link>.
            </p>
          </form>
        </div>

        <p className="mt-4 text-center text-sm opacity-70">
          Already have an account?{" "}
          <Link to={`/login?with=${way}`} state={{ from }} className="link link-secondary font-semibold">
            Log in
          </Link>
        </p>
      </div>
    </div>
  );
}
