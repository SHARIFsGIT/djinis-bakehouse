import { lazy, Suspense, useEffect } from "react";
import { Outlet, Route, Routes, useLocation } from "react-router";
import Navbar from "./components/Navbar";
import Footer from "./components/Footer";
import { Loading } from "./components/Loading";
import { AdminRoute, ProtectedRoute } from "./components/RouteGuards";
import client from "./api/client";
import { applyShopConfig } from "./lib/format";
import { applyBrandColor } from "./lib/theme";

import Home from "./pages/Home";
import Products from "./pages/Products";
import ProductDetail from "./pages/ProductDetail";
import Cart from "./pages/Cart";
import Checkout from "./pages/Checkout";
import BulkOrder from "./pages/BulkOrder";
import Login from "./pages/Login";
import Register from "./pages/Register";
import SocialCallback from "./pages/SocialCallback";
import ForgotPassword from "./pages/ForgotPassword";
import ResetPassword from "./pages/ResetPassword";
import Unsubscribe from "./pages/Unsubscribe";
import NotFound from "./pages/NotFound";
import { PrivacyPolicy, RefundPolicy, TermsOfService } from "./pages/Legal";
import AccountLayout from "./pages/account/AccountLayout";
import Profile from "./pages/account/Profile";
import Addresses from "./pages/account/Addresses";
import Orders from "./pages/account/Orders";
import OrderDetail from "./pages/account/OrderDetail";
import OrderReceipt from "./pages/account/OrderReceipt";
import Saved from "./pages/account/Saved";
import AdminLayout from "./pages/admin/AdminLayout";

/* The baker's desk is more than half of the shop's JavaScript, and no
   customer ever opens it. Each desk page is its own chunk, fetched the first
   time it is visited — so a shopper's first load carries the storefront and
   nothing else. (AdminLayout stays in the main bundle: it is a few lines,
   and it holds the Suspense boundary that keeps the sidebar still while a
   page arrives.)

   A desk left open across a deploy will ask for a chunk whose name no longer
   exists. One reload picks up the new index and its chunk names; the
   session flag keeps a chunk that is genuinely broken from reloading
   forever — that case falls through to the ErrorBoundary. */
const CHUNK_RELOAD_KEY = "bakehouse_chunk_reload";
const deskPage = (load) =>
  lazy(() =>
    load().then(
      (module) => {
        try {
          sessionStorage.removeItem(CHUNK_RELOAD_KEY);
        } catch {}
        return module;
      },
      (error) => {
        let alreadyTried = true;
        try {
          alreadyTried = sessionStorage.getItem(CHUNK_RELOAD_KEY) === "1";
          sessionStorage.setItem(CHUNK_RELOAD_KEY, "1");
        } catch {}
        if (alreadyTried) throw error;
        window.location.reload();
        return new Promise(() => {}); // the page is on its way out
      },
    ),
  );

const Dashboard = deskPage(() => import("./pages/admin/Dashboard"));
const AdminOrders = deskPage(() => import("./pages/admin/Orders"));
const AdminProducts = deskPage(() => import("./pages/admin/Products"));
const Costing = deskPage(() => import("./pages/admin/Costing"));
const AdminReviews = deskPage(() => import("./pages/admin/Reviews"));
const Promotions = deskPage(() => import("./pages/admin/Promotions"));
const AdminUsers = deskPage(() => import("./pages/admin/Users"));
const Marketing = deskPage(() => import("./pages/admin/Marketing"));
const Settings = deskPage(() => import("./pages/admin/Settings"));

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [pathname]);
  return null;
}

function ShopLayout() {
  return (
    <div className="flex min-h-screen flex-col">
      <Navbar />
      <main className="flex-1">
        {/* The net under any lazily-loaded page that has no closer boundary */}
        <Suspense fallback={<Loading />}>
          <Outlet />
        </Suspense>
      </main>
      <Footer />
    </div>
  );
}

export default function App() {
  // The shop's money rules (currency, tax, delivery) before the first basket
  // is tallied — one fetch, mirrored into format.js so every estimate the
  // storefront quotes stays the server's own numbers. A failed fetch leaves
  // the defaults, which match the server's .env. The same answer carries the
  // baker's brand colour: painting it here (and caching it for the next
  // boot's pre-paint) keeps every visitor's theme the server's own pick.
  useEffect(() => {
    client
      .get("/store")
      .then(({ data }) => {
        applyShopConfig(data);
        applyBrandColor(data.brand_color ?? null);
      })
      .catch(() => {});
  }, []);

  return (
    <>
      <div className="grain-overlay" aria-hidden="true" />
      <ScrollToTop />
      <Routes>
        <Route element={<ShopLayout />}>
          <Route index element={<Home />} />
          <Route path="products" element={<Products />} />
          <Route path="products/:slug" element={<ProductDetail />} />
          <Route path="cart" element={<Cart />} />
          <Route path="bulk" element={<BulkOrder />} />
          <Route path="login" element={<Login />} />
          <Route path="register" element={<Register />} />
          <Route path="auth/callback" element={<SocialCallback />} />
          <Route path="forgot-password" element={<ForgotPassword />} />
          <Route path="reset-password" element={<ResetPassword />} />
          <Route path="unsubscribe" element={<Unsubscribe />} />
          <Route path="privacy" element={<PrivacyPolicy />} />
          <Route path="terms" element={<TermsOfService />} />
          <Route path="refunds" element={<RefundPolicy />} />

          <Route element={<ProtectedRoute />}>
            <Route path="checkout" element={<Checkout />} />
            <Route path="account" element={<AccountLayout />}>
              <Route index element={<Orders />} />
              <Route path="orders" element={<Orders />} />
              <Route path="orders/:id" element={<OrderDetail />} />
              <Route path="saved" element={<Saved />} />
              <Route path="addresses" element={<Addresses />} />
              <Route path="profile" element={<Profile />} />
            </Route>
            {/* The receipt stands alone — no account chrome, so what prints
                is the receipt and nothing else. */}
            <Route
              path="account/orders/:id/receipt"
              element={<OrderReceipt />}
            />
          </Route>

          <Route element={<AdminRoute />}>
            <Route path="admin" element={<AdminLayout />}>
              <Route index element={<Dashboard />} />
              <Route path="orders" element={<AdminOrders />} />
              <Route path="products" element={<AdminProducts />} />
              <Route path="costing" element={<Costing />} />
              <Route path="reviews" element={<AdminReviews />} />
              <Route path="promotions" element={<Promotions />} />
              <Route path="users" element={<AdminUsers />} />
              <Route path="marketing" element={<Marketing />} />
              <Route path="settings" element={<Settings />} />
            </Route>
          </Route>

          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </>
  );
}
