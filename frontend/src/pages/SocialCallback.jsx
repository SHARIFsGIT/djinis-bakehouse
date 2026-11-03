import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";

const FRIENDLY_ERRORS = {
  facebook_declined: "The Facebook sign-in was cancelled — no worries, the password works too.",
  facebook_state:
    "That sign-in window expired before it finished. Please start again.",
  facebook_exchange:
    "Facebook didn't confirm the sign-in. Please try again in a moment.",
  facebook_no_email:
    "Facebook didn't share an email address. Sign in with email, or add an email to your Facebook account first.",
  account_disabled: "This account has been deactivated.",
};

/** Where a social callback lands: trade the one-time code in the URL for a
 *  session, or explain plainly why it didn't work. */
export default function SocialCallback() {
  const { exchangeLoginCode } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [error, setError] = useState("");
  const done = useRef(false); // React 18 double-mounts in dev — redeem once

  useEffect(() => {
    if (done.current) return;
    done.current = true;
    const code = params.get("code");
    const failure = params.get("error");
    if (failure) {
      setError(FRIENDLY_ERRORS[failure] ?? "Sign-in didn't complete — please try again.");
      return;
    }
    if (!code) {
      setError("Sign-in didn't complete — please try again.");
      return;
    }
    exchangeLoginCode(code)
      .then((user) => {
        toast.success(`Welcome back, ${user.first_name}`);
        navigate("/", { replace: true });
      })
      .catch(() => setError("That sign-in window expired — please start again."));
    // Runs once per arrival; the URL is what changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-4 px-4 py-24 text-center">
      {error ? (
        <>
          <div className="alert alert-warning items-start text-sm">{error}</div>
          <Link to="/login" className="btn btn-secondary btn-sm">
            Back to log in
          </Link>
        </>
      ) : (
        <>
          <span className="loading loading-spinner" />
          <p className="text-sm opacity-70">Finishing your sign-in…</p>
        </>
      )}
    </div>
  );
}
