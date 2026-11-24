import { Mail, Smartphone } from "lucide-react";
import { useRef } from "react";

const WAYS = [
  { value: "email", label: "Email", Icon: Mail },
  { value: "phone", label: "Phone", Icon: Smartphone },
];

/** The two ways into an account — Email or Phone — as one segmented row
 *  whose pill slides to the chosen side (still, for anyone who asked for
 *  less motion). A tab list by the WAI-ARIA tabs pattern: Tab enters it
 *  once, the arrow keys and Home/End switch, and each tab names the panel
 *  it controls (`${idPrefix}-panel`, which the page draws). */
export default function SignInWay({ value, onChange, idPrefix, label }) {
  const buttons = useRef({});

  const walk = (e) => {
    const index = WAYS.findIndex((w) => w.value === value);
    const next = {
      ArrowRight: WAYS[(index + 1) % WAYS.length],
      ArrowLeft: WAYS[(index + WAYS.length - 1) % WAYS.length],
      Home: WAYS[0],
      End: WAYS[WAYS.length - 1],
    }[e.key];
    if (!next) return;
    e.preventDefault();
    onChange(next.value);
    buttons.current[next.value]?.focus();
  };

  return (
    <div
      role="tablist"
      aria-label={label}
      onKeyDown={walk}
      className="relative grid grid-cols-2 rounded-full bg-base-200 p-1 ring-1 ring-base-300/70"
    >
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-1 left-1 w-[calc(50%-0.25rem)] rounded-full bg-base-100 shadow-sm ring-1 ring-base-300 transition-transform duration-300 ease-[cubic-bezier(0.2,0.7,0.3,1)] motion-reduce:transition-none"
        style={{ transform: value === "phone" ? "translateX(100%)" : "none" }}
      />
      {WAYS.map(({ value: way, label: text, Icon }) => {
        const selected = way === value;
        return (
          <button
            key={way}
            ref={(el) => {
              buttons.current[way] = el;
            }}
            id={`${idPrefix}-tab-${way}`}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={`${idPrefix}-panel`}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(way)}
            className={`relative z-10 flex min-h-10 items-center justify-center gap-2 rounded-full px-3 text-sm font-semibold transition-colors ${
              selected ? "text-secondary" : "text-base-content/70 hover:text-base-content"
            }`}
          >
            <Icon size={16} aria-hidden="true" />
            {text}
          </button>
        );
      })}
    </div>
  );
}
