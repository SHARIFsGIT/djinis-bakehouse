import { Outlet } from "react-router";
import AdminBell from "../../components/AdminBell";
import CornerNav from "../../components/CornerNav";

export default function AdminLayout() {
  return (
    <div className="mx-auto max-w-7xl px-4 py-10">
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <p className="text-xs font-bold tracking-[0.25em] text-secondary uppercase">
            Back of house
          </p>
          <h1 className="font-display mt-1 text-4xl font-semibold">
            Baker&rsquo;s desk
          </h1>
        </div>
        {/* Orders, reviews, leads and signups all ring it */}
        <AdminBell />
      </div>

      <div className="grid gap-8 lg:grid-cols-[240px_1fr]">
        <div className="h-fit lg:sticky lg:top-24">
          <CornerNav ariaLabel="Admin" />
        </div>
        <div className="min-w-0">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
