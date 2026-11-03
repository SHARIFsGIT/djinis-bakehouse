import { useState } from "react";
import { Camera, Trash2 } from "lucide-react";
import client, { apiError, tokens } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { formatDate } from "../../lib/format";
import { shrinkImage } from "../../lib/imageResize";

export default function Profile() {
  const { user, refreshUser } = useAuth();
  const toast = useToast();

  const [form, setForm] = useState({
    first_name: user.first_name,
    last_name: user.last_name,
    phone: user.phone ?? "",
  });
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingAvatar, setSavingAvatar] = useState(false);

  const [passwords, setPasswords] = useState({ current_password: "", new_password: "" });
  const [savingPassword, setSavingPassword] = useState(false);

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
      await client.put("/auth/me", form);
      await refreshUser();
      toast.success("Profile updated");
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setSavingProfile(false);
    }
  };

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

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      {/* Details */}
      <section className="rounded-box glass p-6 shadow-sm ring-1 ring-base-300/60">
        <h2 className="font-display text-2xl font-semibold">Your profile</h2>
        <p className="mt-1 text-sm opacity-60">
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
              {user.first_name.charAt(0)}
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
              <label className="label text-xs" htmlFor="p-last">Last name</label>
              <input
                id="p-last"
                className="input input-bordered w-full"
                required
                maxLength={100}
                value={form.last_name}
                onChange={(e) => setForm((f) => ({ ...f, last_name: e.target.value }))}
              />
            </div>
          </div>
          <div>
            <label className="label text-xs" htmlFor="p-email">Email (login)</label>
            <input id="p-email" className="input input-bordered w-full opacity-60" value={user.email} disabled />
          </div>
          <div>
            <label className="label text-xs" htmlFor="p-phone">Phone</label>
            <input
              id="p-phone"
              className="input input-bordered w-full"
              maxLength={30}
              value={form.phone}
              onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))}
            />
          </div>
          <button type="submit" className="btn btn-primary btn-sm" disabled={savingProfile}>
            {savingProfile ? "Saving…" : "Save changes"}
          </button>
        </form>
      </section>

      {/* Password */}
      <section className="h-fit rounded-box glass p-6 shadow-sm ring-1 ring-base-300/60">
        <h2 className="font-display text-2xl font-semibold">Change password</h2>
        <form onSubmit={changePassword} className="mt-5 space-y-4">
          <div>
            <label className="label text-xs" htmlFor="cur-pass">Current password</label>
            <input
              id="cur-pass"
              type="password"
              className="input input-bordered w-full"
              autoComplete="current-password"
              required
              value={passwords.current_password}
              onChange={(e) => setPasswords((p) => ({ ...p, current_password: e.target.value }))}
            />
          </div>
          <div>
            <label className="label text-xs" htmlFor="new-pass">New password</label>
            <input
              id="new-pass"
              type="password"
              className="input input-bordered w-full"
              placeholder="At least 8 characters"
              autoComplete="new-password"
              required
              minLength={8}
              value={passwords.new_password}
              onChange={(e) => setPasswords((p) => ({ ...p, new_password: e.target.value }))}
            />
          </div>
          <button type="submit" className="btn btn-secondary btn-sm text-secondary-content" disabled={savingPassword}>
            {savingPassword ? "Updating…" : "Update password"}
          </button>
        </form>
      </section>
    </div>
  );
}
