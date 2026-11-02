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
    first_name: "",
    last_name: "",
    email: "",
    phone: "",
    password: "",
  });
  const [busy, setBusy] = useState(false);

  const from = location.state?.from || "/";

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const payload = { ...form };
      if (!payload.phone) delete payload.phone;
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
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label text-xs" htmlFor="first_name">First name</label>
              <input id="first_name" className="input input-bordered w-full" required maxLength={100} value={form.first_name} onChange={set("first_name")} />
            </div>
            <div>
              <label className="label text-xs" htmlFor="last_name">Last name</label>
              <input id="last_name" className="input input-bordered w-full" required maxLength={100} value={form.last_name} onChange={set("last_name")} />
            </div>
          </div>
          <div>
            <label className="label text-xs" htmlFor="reg-email">Email</label>
            <input id="reg-email" type="email" className="input input-bordered w-full" placeholder="you@example.com" autoComplete="email" required value={form.email} onChange={set("email")} />
          </div>
          <div>
            <label className="label text-xs" htmlFor="phone">Phone (optional)</label>
            <input id="phone" className="input input-bordered w-full" maxLength={30} value={form.phone} onChange={set("phone")} />
          </div>
          <div>
            <label className="label text-xs" htmlFor="reg-password">Password</label>
            <input
              id="reg-password"
              type="password"
              className="input input-bordered w-full"
              placeholder="At least 8 characters"
              autoComplete="new-password"
              required
              minLength={8}
              value={form.password}
              onChange={set("password")}
            />
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
