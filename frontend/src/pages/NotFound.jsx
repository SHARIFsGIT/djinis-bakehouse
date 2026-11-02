import { Link } from "react-router";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-2xl px-4 py-24 text-center">
      <h1 className="font-display mt-6 text-5xl font-semibold">4<span className="text-secondary">0</span>4</h1>
      <p className="font-display mt-3 text-2xl italic opacity-80">This page is still proofing.</p>
      <p className="mt-3 opacity-70">
        Whatever you were looking for isn&rsquo;t in the case — but plenty of other good things are.
      </p>
      <Link to="/" className="btn btn-primary btn mt-8">Back to the bakery</Link>
    </div>
  );
}
