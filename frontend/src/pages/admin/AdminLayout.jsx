import { Suspense } from "react";
import { Outlet } from "react-router";
import CornerNav from "../../components/CornerNav";
import { Loading } from "../../components/Loading";

export default function AdminLayout() {
  return (
    <div className="mx-auto max-w-7xl px-4 py-10">
      {/* The heading stands on its own — the bell lives in the navbar, and
          rings wherever the baker is. */}
      <h1 className="font-display mb-6 text-3xl font-semibold sm:text-4xl">Baker&rsquo;s desk</h1>

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
