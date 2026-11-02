import { useEffect, useState } from "react";
import client, { apiError } from "../../api/client";
import { formatDate } from "../../lib/format";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { Loading } from "../../components/Loading";

export default function AdminUsers() {
  const toast = useToast();
  const { user: me } = useAuth();
  const [users, setUsers] = useState(null);

  const load = () => client.get("/admin/users").then(({ data }) => setUsers(data));

  useEffect(() => {
    load();
  }, []);

  const update = async (id, payload) => {
    try {
      await client.put(`/admin/users/${id}`, payload);
      await load();
      toast.success("User updated");
    } catch (err) {
      toast.error(apiError(err));
    }
  };

  if (users === null) return <Loading label="Rolling out the regulars…" />;

  return (
    <div className="overflow-x-auto rounded-box glass shadow-sm ring-1 ring-base-300/60">
      <table className="table table-sm">
        <thead>
          <tr className="text-xs uppercase opacity-50">
            <th>Customer</th>
            <th>Role</th>
            <th>Joined</th>
            <th>Active</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id} className="hover:bg-base-200/50">
              <td>
                <div className="flex items-center gap-3">
                  {u.avatar_url ? (
                    <img
                      src={u.avatar_url}
                      alt=""
                      className="h-9 w-9 shrink-0 rounded-full object-cover ring-1 ring-base-300"
                    />
                  ) : (
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-secondary/15 text-sm font-bold text-secondary">
                      {u.first_name?.[0]?.toUpperCase() ?? "?"}
                    </span>
                  )}
                  <div>
                    <p className="font-semibold">
                      {u.first_name} {u.last_name}
                      {u.id === me.id && <span className="ml-1 text-xs opacity-50">(you)</span>}
                    </p>
                    <p className="text-xs opacity-60">{u.email}{u.phone ? ` · ${u.phone}` : ""}</p>
                  </div>
                </div>
              </td>
              <td>
                <select
                  className="select select-bordered select-xs"
                  value={u.role}
                  onChange={(e) => update(u.id, { role: e.target.value })}
                  aria-label={`Role for ${u.email}`}
                >
                  <option value="customer">customer</option>
                  <option value="admin">admin</option>
                </select>
              </td>
              <td className="text-xs opacity-60">{formatDate(u.created_at)}</td>
              <td>
                <input
                  type="checkbox"
                  className="toggle toggle-success toggle-sm"
                  checked={u.is_active}
                  disabled={u.id === me.id}
                  onChange={(e) => update(u.id, { is_active: e.target.checked })}
                  aria-label={`Active toggle for ${u.email}`}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
