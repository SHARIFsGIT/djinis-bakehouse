import { Eye, EyeOff } from "lucide-react";
import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { apiError } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";

export default function Register() {
  const { register } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();

  const [form, setForm] = useState({
    name: "",
    email: "",
    phone: "",
    password: "",
  });
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);

  const from = location.state?.from || "/";

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    // One "Your name" field: the first word is the first name, whatever
    // follows is the last — a single name is fine too.
    const parts = form.name.trim().split(/\s+/);
    if (!parts[0]) return;
    setBusy(true);
    try {
      const payload = {
        first_name: parts[0],
        last_name: parts.slice(1).join(" "),
        email: form.email,
        password: form.password,
      };
      if (form.phone.trim()) payload.phone = form.phone.trim();
      const user = await register(payload);
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
      <div className="rise-in rounded-box glass p-8 shadow-lg ring-1 ring-base-300/60">
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
              placeholder="Jamie Oliver"
              autoComplete="name"
              required
              maxLength={200}
              value={form.name}
              onChange={set("name")}
            />
          </div>
          <div>
            <label className="label text-xs" htmlFor="reg-email">Email</label>
            <input id="reg-email" type="email" className="input input-bordered w-full" placeholder="you@example.com" autoComplete="email" required value={form.email} onChange={set("email")} />
          </div>
          <div>
            <label className="label text-xs" htmlFor="phone">Phone (optional)</label>
            <input id="phone" className="input input-bordered w-full" maxLength={30} autoComplete="tel" value={form.phone} onChange={set("phone")} />
          </div>
          <div>
            <label className="label text-xs" htmlFor="reg-password">Password</label>
            <div className="relative">
              <input
                id="reg-password"
                type={showPassword ? "text" : "password"}
                className="input input-bordered w-full pr-11"
                placeholder="At least 8 characters"
                autoComplete="new-password"
                required
                minLength={8}
                value={form.password}
                onChange={set("password")}
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
            {busy ? <span className="loading loading-spinner loading-sm" /> : "Create account"}
          </button>
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
