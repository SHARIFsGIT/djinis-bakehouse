import { Minus, Plus } from "lucide-react";

export default function QuantityInput({ value, onChange, max = 99, min = 1, size = "" }) {
  const clamp = (n) => Math.max(min, Math.min(max, Number.isFinite(n) ? n : min));
  return (
    <div className={`join ${size}`} role="group" aria-label="Quantity">
      <button
        type="button"
        className="btn btn-primary btn-sm join-item"
        onClick={() => onChange(clamp(value - 1))}
        disabled={value <= min}
        aria-label="Decrease quantity"
      >
        <Minus size={14} aria-hidden="true" />
      </button>
      {/* Read out as it changes, so a screen reader hears the new count. */}
      <span
        className="input input-sm join-item input-bordered figures w-12 justify-center text-center font-bold"
        aria-live="polite"
      >
        {value}
      </span>
      <button
        type="button"
        className="btn btn-primary btn-sm join-item"
        onClick={() => onChange(clamp(value + 1))}
        disabled={value >= max}
        aria-label="Increase quantity"
      >
        <Plus size={14} aria-hidden="true" />
      </button>
    </div>
  );
}
