import { Eye, EyeOff } from "lucide-react";
import { useState } from "react";

/** A password field that always carries its own show/hide eye. The same
 *  markup that used to be copy-pasted through the auth pages, now written
 *  once — every password the shop asks for can be checked before it's
 *  sent. Takes the usual input props (id, value, onChange, autoComplete…)
 *  plus a label; `className` lands on the input itself. */
export default function PasswordInput({ label, className = "", ...input }) {
  const [show, setShow] = useState(false);
  return (
    <div>
      {label && (
        <label className="label text-xs" htmlFor={input.id}>
          {label}
        </label>
      )}
      <div className="relative">
        <input
          type={show ? "text" : "password"}
          className={`input input-bordered w-full pr-11 ${className}`}
          {...input}
        />
        <button
          type="button"
          className="btn btn-ghost btn-xs btn-square absolute top-1/2 right-1.5 -translate-y-1/2"
          onClick={() => setShow((v) => !v)}
          aria-label={show ? "Hide password" : "Show password"}
        >
          {show ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
      </div>
    </div>
  );
}
