import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { Camera, CircleCheck, MessageSquare, ShieldAlert, Trash2 } from "lucide-react";
import client, { apiError, tokens } from "../../api/client";
import PasswordInput from "../../components/PasswordInput";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { formatDate } from "../../lib/format";
import { shrinkImage } from "../../lib/imageResize";
import { fetchStore } from "../../lib/store";

export default function Profile() {
  const { user, refreshUser, logout } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();

  const [form, setForm] = useState({
    first_name: user.first_name,
    last_name: user.last_name,
    phone: user.phone ?? "",
  });
  // Only for an account opened with a phone number: the address it can add.
  const [newEmail, setNewEmail] = useState("");
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingAvatar, setSavingAvatar] = useState(false);

  const [passwords, setPasswords] = useState({ current_password: "", new_password: "" });
  const [savingPassword, setSavingPassword] = useState(false);

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmPassword, setConfirmPassword] = useState("");
  const [deleting, setDeleting] = useState(false);

  // Text sign-in: offered only where the shop can send texts, and a number
  // is trusted for it only once a code texted to it has come back.
  const [textSignIn, setTextSignIn] = useState(false);
  const [codeOut, setCodeOut] = useState(null); // what the shop said when it sent one
  const [smsCode, setSmsCode] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [codeProblem, setCodeProblem] = useState("");

  useEffect(() => {
    fetchStore()
      .then((data) => setTextSignIn(Boolean(data.phone_login)))
      .catch(() => {});
  }, []);

  // A saved number is shown the way it is stored; a new number starts with
  // no code out.
  useEffect(() => {
    setForm((f) => ({ ...f, phone: user.phone ?? "" }));
    setCodeOut(null);
    setSmsCode("");
    setCodeProblem("");
  }, [user.phone]);

  const pickAvatar = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setSavingAvatar(true);
    try {
      const shrunk = await shrinkImage(file, 512);
      const data = new FormData();
      data.append("photo", shrunk);
      await client.post("/auth/me/avatar", data);
      await refreshUser();
      toast.success("Photo updated");
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setSavingAvatar(false);
    }
  };

  const removeAvatar = async () => {
    try {
      await client.delete("/auth/me/avatar");
      await refreshUser();
      toast.info("Photo removed");
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  const saveProfile = async (e) => {
    e.preventDefault();
    setSavingProfile(true);
    try {
      await client.put("/auth/me", {
        ...form,
        ...(!user.email && newEmail.trim() ? { email: newEmail.trim() } : {}),
      });
      await refreshUser();
      setNewEmail("");
      toast.success("Profile updated");
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setSavingProfile(false);
    }
  };

  const sendCode = async () => {
    setConfirming(true);
    setCodeProblem("");
    try {
      const { data } = await client.post("/auth/me/phone/confirm");
      if (data.phone_verified) {
        await refreshUser();
        toast.info(data.message);
        return;
      }
      setCodeOut(data.message);
      setSmsCode("");
    } catch (err) {
      toast.error(apiError(err, "Couldn't send a code"));
    } finally {
      setConfirming(false);
    }
  };

  const checkCode = async (e) => {
    e.preventDefault();
    if (smsCode.length !== 6) return;
    setConfirming(true);
    setCodeProblem("");
    try {
      await client.post("/auth/me/phone/confirm/check", { code: smsCode });
      await refreshUser();
      setCodeOut(null);
      setSmsCode("");
      toast.success("Number confirmed — you can sign in with a texted code now");
    } catch (err) {
      setCodeProblem(apiError(err, "Couldn't confirm the code"));
    } finally {
      setConfirming(false);
    }
  };

  const phoneSaved = form.phone === (user.phone ?? "");

  const changePassword = async (e) => {
    e.preventDefault();
    setSavingPassword(true);
    try {
      const { data } = await client.put("/auth/me/password", passwords);
      // Changing the password revokes every session; keep this one signed in.
      tokens.save(data.access_token, data.refresh_token);
      setPasswords({ current_password: "", new_password: "" });
      toast.success("Password changed — other devices have been signed out");
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setSavingPassword(false);
    }
  };

  const deleteAccount = async (e) => {
    e.preventDefault();
    setDeleting(true);
    try {
      await client.post("/auth/me/delete", { password: confirmPassword });
      logout();
      navigate("/");
      toast.info("Your account has been deleted — take care");
    } catch (err) {
      toast.error(apiError(err));
      setDeleting(false);
    }
  };

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      {/* Left column: who you are */}
      <section className="rounded-box glass p-6 shadow-sm ring-1 ring-base-300/60">
        <h2 className="font-display text-2xl font-semibold">Your profile</h2>
        <p className="mt-1 text-sm opacity-70">
          Member since {formatDate(user.created_at)} · {user.role === "admin" ? "Head baker" : "Regular"}
        </p>

        <div className="mt-5 flex items-center gap-4">
          {user.avatar_url ? (
            <img
              src={user.avatar_url}
              alt="Your photo"
              className="h-16 w-16 rounded-full object-cover ring-2 ring-base-300"
            />
          ) : (
            <span className="brand-gradient flex h-16 w-16 items-center justify-center rounded-full text-xl font-bold text-secondary-content">
              {(user.first_name || user.email || "?").charAt(0).toUpperCase()}
            </span>
          )}
          <div className="space-y-1.5">
            <label className={`btn btn-outline btn-sm ${savingAvatar ? "loading" : ""}`}>
              <Camera size={18} />
              {user.avatar_url ? "Replace photo" : "Upload photo"}
              <input
                type="file"
                accept="image/*"
                className="hidden"
                onChange={pickAvatar}
                disabled={savingAvatar}
                aria-label="Profile photo"
              />
            </label>
            {user.avatar_url && (
              <button type="button" className="btn btn-ghost btn-xs text-error" onClick={removeAvatar}>
                <Trash2 size={14} /> Remove
              </button>
            )}
          </div>
        </div>

        <form onSubmit={saveProfile} className="mt-6 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label text-xs" htmlFor="p-first">First name</label>
              <input
                id="p-first"
                className="input input-bordered w-full"
                required
                maxLength={100}
                value={form.first_name}
                onChange={(e) => setForm((f) => ({ ...f, first_name: e.target.value }))}
              />
            </div>
            <div>
              {/* Optional, as at sign-up: one name is a whole name. */}
              <label className="label text-xs" htmlFor="p-last">Last name</label>
              <input
                id="p-last"
                className="input input-bordered w-full"
                maxLength={100}
                value={form.last_name}
                onChange={(e) => setForm((f) => ({ ...f, last_name: e.target.value }))}
              />
            </div>
          </div>
          {user.email ? (
            <div>
              <label className="label text-xs" htmlFor="p-email">Email (login)</label>
              <input id="p-email" className="input input-bordered w-full opacity-70" value={user.email} disabled />
            </div>
          ) : (
            // Opened with a phone number: there is no address yet, and this
            // is where one is added. Once saved it is the account's, and is
            // shown above like anyone else's.
            <div>
              <label className="label text-xs" htmlFor="p-email">Email (optional)</label>
              <input
                id="p-email"
                type="email"
                className="input input-bordered w-full"
                placeholder="you@example.com"
                autoComplete="email"
                maxLength={255}
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
              />
              <p className="mt-1 text-xs opacity-70">
                You log in with your phone number. Add an email to get receipts and order updates
                by mail, and to be able to reset a forgotten password yourself.
              </p>
            </div>
          )}
          <div>
            <label className="label text-xs" htmlFor="p-phone">
              {user.email ? "Phone" : "Phone (login)"}
            </label>
            <input
              id="p-phone"
              type="tel"
              autoComplete="tel"
              className="input input-bordered w-full"
              maxLength={30}
              required={!user.email}
              value={form.phone}
              onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))}
            />
            <p className="mt-1 text-xs opacity-70">
              You can log in with this number too. Start it with 0, or with + and your country code.
            </p>
          </div>
          <button type="submit" className="btn btn-primary btn-sm" disabled={savingProfile}>
            {savingProfile ? "Saving…" : "Save changes"}
          </button>
        </form>

        {/* Text sign-in: customers only (admins keep their password), and
            only for a number proven by a code texted to it. Its own form,
            so Enter in the code field never saves the profile. */}
        {textSignIn && user.role !== "admin" && user.phone && (
          <div className="mt-6 border-t border-base-300/70 pt-5">
            <h3 className="text-sm font-semibold">Text sign-in</h3>
            {!phoneSaved ? (
              <p className="mt-1.5 text-xs opacity-70">
                Save your new number first, then confirm it with a texted code.
              </p>
            ) : user.phone_verified ? (
              <p className="mt-1.5 flex items-start gap-2 text-sm">
                <CircleCheck size={16} className="mt-0.5 shrink-0 text-success" aria-hidden="true" />
                <span>Your number is confirmed — you can sign in with a code texted to it.</span>
              </p>
            ) : codeOut ? (
              <form onSubmit={checkCode} className="mt-2 space-y-3">
                <p className="text-sm">{codeOut}</p>
                <div>
                  <label className="label text-xs" htmlFor="p-sms-code">Code from the text</label>
                  <div className="flex flex-wrap gap-2">
                    <input
                      id="p-sms-code"
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      className="input input-bordered w-40 text-center font-mono tracking-[0.3em]"
                      placeholder="······"
                      maxLength={6}
                      required
                      value={smsCode}
                      onChange={(e) => {
                        setSmsCode(e.target.value.replace(/\D/g, ""));
                        setCodeProblem("");
                      }}
                      aria-invalid={Boolean(codeProblem)}
                      aria-describedby={codeProblem ? "p-sms-problem" : undefined}
                    />
                    <button
                      type="submit"
                      className="btn btn-primary"
                      disabled={confirming || smsCode.length !== 6}
                    >
                      {confirming ? <span className="loading loading-spinner loading-sm" /> : "Confirm number"}
                    </button>
                  </div>
                  {codeProblem && (
                    <p id="p-sms-problem" role="alert" className="mt-1.5 text-xs font-semibold text-error">
                      {codeProblem}
                    </p>
                  )}
                </div>
                <button
                  type="button"
                  className="link link-secondary text-xs"
                  onClick={sendCode}
                  disabled={confirming}
                >
                  Send a new code
                </button>
              </form>
            ) : (
              <div className="mt-1.5">
                <p className="text-xs opacity-70">
                  Not confirmed yet. Confirm your number to sign in with a code texted to it,
                  instead of your password.
                </p>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm mt-3 border border-base-300"
                  onClick={sendCode}
                  disabled={confirming}
                >
                  {confirming ? (
                    <span className="loading loading-spinner loading-xs" aria-hidden="true" />
                  ) : (
                    <MessageSquare size={15} aria-hidden="true" />
                  )}
                  Text me a code
                </button>
              </div>
            )}
          </div>
        )}
      </section>

      {/* Right column: your sign-in, then your data directly below it */}
      <div className="flex flex-col gap-6">
      <section className="h-fit rounded-box glass p-6 shadow-sm ring-1 ring-base-300/60">
        <h2 className="font-display text-2xl font-semibold">Change password</h2>
        <form onSubmit={changePassword} className="mt-5 space-y-4">
          <PasswordInput
            id="cur-pass"
            label="Current password"
            autoComplete="current-password"
            required
            value={passwords.current_password}
            onChange={(e) => setPasswords((p) => ({ ...p, current_password: e.target.value }))}
          />
          <PasswordInput
            id="new-pass"
            label="New password"
            placeholder="At least 8 characters"
            autoComplete="new-password"
            required
            minLength={8}
            value={passwords.new_password}
            onChange={(e) => setPasswords((p) => ({ ...p, new_password: e.target.value }))}
          />
          <button type="submit" className="btn btn-secondary btn-sm text-secondary-content" disabled={savingPassword}>
            {savingPassword ? "Updating…" : "Update password"}
          </button>
        </form>
      </section>

      {/* Your account — close it, on your own say-so */}
      <section className="h-fit rounded-box glass p-6 shadow-sm ring-1 ring-base-300/60">
        <h2 className="font-display text-2xl font-semibold">Close your account</h2>
        <p className="mt-1 text-sm opacity-70">
          Closing the account erases your personal details — only anonymized
          invoices remain, as the law requires. Your password confirms it&rsquo;s
          really you.
        </p>
        <p className="mt-2 text-xs opacity-70">
          Signed up with Facebook and never set a password? Use{" "}
          <Link to="/forgot-password" className="link link-secondary">forgot password</Link>{" "}
          to choose one first.
        </p>
        <div className="mt-4">
          <button
            type="button"
            className="btn btn-ghost btn-sm text-error"
            onClick={() => setConfirmOpen((open) => !open)}
          >
            <Trash2 size={16} /> Delete my account
          </button>
        </div>

        {confirmOpen && (
          <form
            onSubmit={deleteAccount}
            className="mt-4 rounded-box border border-error/30 bg-error/5 p-4"
          >
            <p className="flex items-center gap-2 text-sm font-semibold text-error">
              <ShieldAlert size={16} className="shrink-0" />
              This closes the account for good
            </p>
            <p className="mt-1 text-xs opacity-70">
              Your name, contact details, saved bakes and reviews are erased.
              Past orders stay as anonymous invoices. This can&rsquo;t be
              undone — confirm with your password.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <div className="w-full max-w-xs">
                <PasswordInput
                  required
                  autoComplete="current-password"
                  placeholder="Your password"
                  className="input-sm"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  aria-label="Confirm with your password"
                />
              </div>
              <button type="submit" className="btn btn-error btn-sm" disabled={deleting}>
                {deleting ? "Deleting…" : "Delete everything"}
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  setConfirmOpen(false);
                  setConfirmPassword("");
                }}
              >
                Keep my account
              </button>
            </div>
          </form>
        )}
      </section>
      </div>
    </div>
  );
}
