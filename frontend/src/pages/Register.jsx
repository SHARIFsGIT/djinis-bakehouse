import { useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router";
import { apiError } from "../api/client";
import PasswordInput from "../components/PasswordInput";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { returnPath } from "../lib/navigation";
import { identify, looksLikePhone, saysItsCountry } from "../lib/signin";

export default function Register() {
  const { register, user, ready } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();

  // Three things to type: a name, one way in (an email address or a phone
  // number — whichever the customer will remember), a password.
  const [form, setForm] = useState({ name: "", who: "", password: "" });
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);

  const from = returnPath(location);

  const set = (key) => (e) => {
    setForm((f) => ({ ...f, [key]: e.target.value }));
    if (key === "who") setProblem("");
  };

  // Already signed in — nothing to create.
  if (ready && user && !busy) return <Navigate to={from} replace />;

  const byPhone = looksLikePhone(form.who);

  const submit = async (e) => {
    e.preventDefault();
    // One "Your name" field: the first word is the first name, whatever
    // follows is the last — a single name is fine too.
    const parts = form.name.trim().split(/\s+/);
    if (!parts[0]) return;
    const id = identify(form.who);
    if (!id) {
      setProblem("Enter an email address or a phone number — you'll log in with it.");
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
      toast.success(`Welcome to the bakehouse, ${user.first_name}`);
      navigate(from, { replace: true });
    } catch (err) {
      toast.error(apiError(err, "Couldn't create the account"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-md px-4 py-16">
      {/* As on the log-in page: under 390px the card gives its sides 20px
          instead of 32 so the email field has room to show its whole hint. */}
      <div className="rise-in rounded-box glass px-5 py-8 shadow-lg ring-1 ring-base-300/60 min-[390px]:px-8">
        <img src="/static/site/logo.png" alt="" className="mx-auto h-14 w-14 object-contain" />
        <h1 className="font-display mt-4 text-center text-3xl font-semibold">Join the bakehouse</h1>
        <p className="mt-1 text-center text-sm opacity-70">
          Faster checkout, order history, first dibs on seasonal bakes.
        </p>

        <form onSubmit={submit} className="mt-6 space-y-4">
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
          <div>
            <label className="label text-xs" htmlFor="reg-email">Email or phone number</label>
            <input
              id="reg-email"
              type="text"
              inputMode="email"
              // The hint alone steps down on a narrow phone (15px, then 13px
              // under 360px), as on the log-in page; typed text stays 16px.
              className={`input input-bordered w-full max-[389px]:placeholder:text-[15px] max-[359px]:placeholder:text-[13px] ${problem ? "input-error" : ""}`}
              placeholder="you@example.com or 0176 555 0100"
              autoComplete="username"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              required
              maxLength={255}
              aria-invalid={Boolean(problem)}
              aria-describedby="reg-who-note"
              value={form.who}
              onChange={set("who")}
            />
            {/* One line under the field, saying what matters for what was
                typed: a problem, what a phone number alone means, or simply
                what the field is for. */}
            <p
              id="reg-who-note"
              className={`mt-1.5 text-xs ${problem ? "font-semibold text-error" : "opacity-70"}`}
              role={problem ? "alert" : undefined}
            >
              {problem ||
                (byPhone
                  ? "You'll log in with this number. Receipts and password resets go by email — add one in your profile whenever you like."
                  : "Whichever you'll remember — it's what you log in with.")}
            </p>
          </div>
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
          <button type="submit" className="btn btn-primary btn w-full" disabled={busy}>
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

        <p className="mt-4 text-center text-sm opacity-70">
          Already have an account?{" "}
          <Link to="/login" state={{ from }} className="link link-secondary font-semibold">
            Log in
          </Link>
        </p>
      </div>
    </div>
  );
}
