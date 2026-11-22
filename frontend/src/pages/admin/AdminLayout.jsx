import { Suspense } from "react";
import { Outlet } from "react-router";
import CornerNav from "../../components/CornerNav";
import { Loading } from "../../components/Loading";

export default function AdminLayout() {
  return (
    <div className="mx-auto max-w-7xl px-4 py-10">
      <div className="mb-6">
        <p className="text-xs font-bold tracking-[0.25em] text-secondary uppercase">
          Back of house
        </p>
        <h1 className="font-display mt-1 text-4xl font-semibold">
          Baker&rsquo;s desk
        </h1>
        {/* The bell lives in the navbar now — it rings wherever the baker is */}
      </div>

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[240px_1fr]">
        <div className="h-fit lg:sticky lg:top-24">
          <CornerNav ariaLabel="Admin" />
        </div>
        <div className="min-w-0">
          {/* Desk pages load on first visit (see App.jsx) — the boundary sits
              here so the header and sidebar hold still while one arrives. */}
          <Suspense fallback={<Loading label="Opening the desk…" />}>
            <Outlet />
          </Suspense>
        </div>
      </div>
    </div>
  );
}
