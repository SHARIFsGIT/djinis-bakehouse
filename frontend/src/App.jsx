import { useEffect } from "react";
import { Outlet, Route, Routes, useLocation } from "react-router";
import Navbar from "./components/Navbar";
import Footer from "./components/Footer";
import { AdminRoute, ProtectedRoute } from "./components/RouteGuards";

import Home from "./pages/Home";
import Products from "./pages/Products";
import ProductDetail from "./pages/ProductDetail";
import Cart from "./pages/Cart";
import Checkout from "./pages/Checkout";
import BulkOrder from "./pages/BulkOrder";
import Login from "./pages/Login";
import Register from "./pages/Register";
import NotFound from "./pages/NotFound";
import AccountLayout from "./pages/account/AccountLayout";
import Profile from "./pages/account/Profile";
import Addresses from "./pages/account/Addresses";
import Orders from "./pages/account/Orders";
import OrderDetail from "./pages/account/OrderDetail";
import Saved from "./pages/account/Saved";
import AdminLayout from "./pages/admin/AdminLayout";
import Dashboard from "./pages/admin/Dashboard";
import AdminOrders from "./pages/admin/Orders";
import AdminProducts from "./pages/admin/Products";
import Promotions from "./pages/admin/Promotions";
import AdminUsers from "./pages/admin/Users";
import Settings from "./pages/admin/Settings";

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
        <Outlet />
      </main>
      <Footer />
    </div>
  );
}

export default function App() {
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
          </Route>

          <Route element={<AdminRoute />}>
            <Route path="admin" element={<AdminLayout />}>
              <Route index element={<Dashboard />} />
              <Route path="orders" element={<AdminOrders />} />
              <Route path="products" element={<AdminProducts />} />
              <Route path="promotions" element={<Promotions />} />
              <Route path="users" element={<AdminUsers />} />
              <Route path="settings" element={<Settings />} />
            </Route>
          </Route>

          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </>
  );
}
