import { Navigate, Outlet, useLocation } from "react-router";
import { useAuth } from "../context/AuthContext";
import { Loading } from "./Loading";

export function ProtectedRoute() {
  const { user, ready } = useAuth();
  const location = useLocation();

  if (!ready) return <Loading label="Checking the recipe book…" />;
  if (!user) {
    return <Navigate to="/login" state={{ from: location.pathname + location.search }} replace />;
  }
  return <Outlet />;
}

export function AdminRoute() {
  const { user, ready, isAdmin } = useAuth();
  const location = useLocation();

  if (!ready) return <Loading label="Checking the recipe book…" />;
  if (!user) {
    return <Navigate to="/login" state={{ from: location.pathname + location.search }} replace />;
  }
  if (!isAdmin) return <Navigate to="/" replace />;
  return <Outlet />;
}
