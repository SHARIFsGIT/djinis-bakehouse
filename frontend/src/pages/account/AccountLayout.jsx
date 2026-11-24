import { Outlet } from "react-router";
import { useAuth } from "../../context/AuthContext";
import CornerNav from "../../components/CornerNav";

export default function AccountLayout() {
  const { isAdmin } = useAuth();

  return (
    <div className="mx-auto max-w-7xl px-4 py-10">
      <h1 className="font-display text-4xl font-semibold">My corner</h1>
      {/* A customer's tabs right below already list what is here. */}
      {isAdmin && (
        <p className="mt-1 opacity-70">Your own details, with the whole desk one click away.</p>
      )}

      <div className="dash-divider mt-6 grid grid-cols-1 gap-8 pt-6 lg:grid-cols-[240px_1fr]">
        <div className="h-fit lg:sticky lg:top-24">
          <CornerNav ariaLabel="Account" />
        </div>
        <div>
          <Outlet />
        </div>
      </div>
    </div>
  );
}
