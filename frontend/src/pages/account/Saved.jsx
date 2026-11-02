import { useEffect, useState } from "react";
import { Link } from "react-router";
import client from "../../api/client";
import ProductCard from "../../components/ProductCard";
import EmptyState from "../../components/EmptyState";
import { ProductGridSkeleton } from "../../components/Loading";

export default function Saved() {
  const [products, setProducts] = useState(null);

  // The heart buttons keep their own state; refresh the list whenever it's
  // shown so an un-hearted bake disappears right away.
  useEffect(() => {
    let cancelled = false;
    client
      .get("/products/favorites")
      .then(({ data }) => !cancelled && setProducts(data))
      .catch(() => !cancelled && setProducts([]));
    return () => {
      cancelled = true;
    };
  }, []);

  if (products === null) return <ProductGridSkeleton />;

  if (products.length === 0) {
    return (
      <EmptyState
        title="Nothing saved yet"
        hint="Tap the heart on any bake and it will wait for you here — perfect for planning the weekend box."
      >
        <Link to="/products" className="btn btn-primary btn">Browse the case</Link>
      </EmptyState>
    );
  }

  return (
    <div>
      <p className="mb-6 opacity-70">
        {products.length} {products.length === 1 ? "bake" : "bakes"} waiting for their moment.
      </p>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-4">
        {products.map((p) => (
          <ProductCard key={p.id} product={p} />
        ))}
      </div>
    </div>
  );
}
