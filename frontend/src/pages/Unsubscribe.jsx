import { MailX } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import client, { apiError } from "../api/client";

/**
 * The landing page behind every marketing email's one-click unsubscribe.
 * The link itself is the customer's decision — this page processes it
 * immediately (GDPR: no extra hoops) and confirms in plain words. Order
 * receipts and account mail keep arriving; only marketing stops.
 */
export default function Unsubscribe() {
  const [params] = useSearchParams();
  const token = params.get("token");
  // One request per mount, never per re-render.
  const requested = useRef(false);
  const [state, setState] = useState({ status: "working", message: "" });

  useEffect(() => {
    if (requested.current) return;
    requested.current = true;
    if (!token) {
      setState({ status: "invalid", message: "This link is missing its key — use the button inside one of our emails." });
      return;
    }
    client
      .post("/marketing/unsubscribe", { token })
      .then(({ data }) =>
        setState({
          status: "done",
          message: `You're off the list${data.first_name ? `, ${data.first_name}` : ""}. No more news from us — sorry to see you go.`,
        })
      )
      .catch((err) =>
        setState({ status: "invalid", message: apiError(err, "This link isn't valid anymore.") })
      );
  }, [token]);

  return (
    <div className="mx-auto flex min-h-[50vh] max-w-lg flex-col items-center justify-center px-4 py-16 text-center">
      <span className="brand-gradient flex h-16 w-16 items-center justify-center rounded-full text-secondary-content">
        <MailX size={30} aria-hidden="true" />
      </span>
      <h1 className="mt-6 font-display text-3xl font-semibold">
        {state.status === "working" ? "One moment…" : state.status === "done" ? "You're unsubscribed" : "Hmm, that link"}
      </h1>
      <p className="mt-3 text-sm opacity-75">{state.message || "Processing your unsubscribe…"}</p>
      {state.status !== "working" && (
        <p className="mt-2 text-xs opacity-60">
          Receipts and updates about orders you've placed still arrive — they're not marketing.
        </p>
      )}
      <Link to="/" className="btn btn-primary btn-sm mt-8">Back to the bakery</Link>
    </div>
  );
}
