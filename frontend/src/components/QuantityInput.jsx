export default function QuantityInput({ value, onChange, max = 99, min = 1, size = "" }) {
  const clamp = (n) => Math.max(min, Math.min(max, Number.isFinite(n) ? n : min));
  return (
    <div className={`join ${size}`}>
      <button
        type="button"
        className="btn btn-primary btn-sm join-item"
        onClick={() => onChange(clamp(value - 1))}
        disabled={value <= min}
        aria-label="Decrease quantity"
      >
        −
      </button>
      <span className="input input-sm join-item input-bordered w-12 text-center font-bold">
        {value}
      </span>
      <button
        type="button"
        className="btn btn-primary btn-sm join-item"
        onClick={() => onChange(clamp(value + 1))}
        disabled={value >= max}
        aria-label="Increase quantity"
      >
        +
      </button>
    </div>
  );
}
