import { useEffect, useLayoutEffect, useState } from "react";
import { money, shopConfig } from "../lib/format";

/**
 * The desk's trading chart: one column per day, the last 30, in the shop's
 * active currency. A single series needs no legend — the caption names what
 * is plotted — and the colour never carries meaning the axis doesn't
 * already say.
 *
 * Chart grammar (kept deliberately quiet so the data is the loud thing):
 * columns cap at 24px, stand square on the baseline and round at the data
 * end, with air between neighbours; hairline gridlines one step off the
 * surface; clean compact y-ticks; text in ink tokens (never the series
 * colour).
 *
 * It is drawn in real pixels — the plot is measured, not scaled — so the
 * labels are the same size on a phone as on a desk. Every figure can be
 * reached three ways: a pointer or a finger on the plot, the arrow keys,
 * or the table.
 */

const PAD = { top: 14, bottom: 26, left: 46, right: 8 };

/** A column's outline: square where it stands, rounded where it ends. */
const column = (x, top, width, base, r) =>
  `M${x},${base}V${top + r}Q${x},${top} ${x + r},${top}H${x + width - r}` +
  `Q${x + width},${top} ${x + width},${top + r}V${base}Z`;

const dayLabel = (day, options = { month: "short", day: "numeric" }) =>
  new Date(`${day.date}T12:00:00`).toLocaleDateString("en-US", options);

/** An axis figure: the currency, no cents, thousands folded ("€20", "€1.5K"). */
const axisMoney = (value) => {
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: shopConfig.currency,
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(value);
  } catch {
    return String(Math.round(value));
  }
};

const VIEWS = [
  { value: "chart", label: "Chart" },
  { value: "table", label: "Table" },
];

export default function TradingChart({ series }) {
  const days = series ?? [];
  const [view, setView] = useState("chart");
  const [plot, setPlot] = useState(null); // the element the chart is drawn in
  const [width, setWidth] = useState(720);
  const [picked, setPicked] = useState(null); // the day being read, by index

  // Measured, so a pixel in the drawing is a pixel on the screen.
  useLayoutEffect(() => {
    if (!plot) return undefined;
    const measure = () => setWidth(Math.max(220, Math.round(plot.clientWidth)));
    measure();
    if (typeof ResizeObserver !== "function") return undefined;
    const watcher = new ResizeObserver(measure);
    watcher.observe(plot);
    return () => watcher.disconnect();
  }, [plot]);

  // A finger has no "leave": the day stays read until something else is
  // touched.
  useEffect(() => {
    if (picked === null || !plot) return undefined;
    const away = (e) => {
      if (!plot.contains(e.target)) setPicked(null);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [picked, plot]);

  const peak = Math.max(...days.map((d) => d.revenue), 0);
  if (peak <= 0) {
    return (
      <p className="mt-4 text-sm opacity-70">
        No orders in these 30 days yet — the chart fills as they come in.
      </p>
    );
  }

  const height = width < 480 ? 200 : 240;
  const plotWidth = width - PAD.left - PAD.right;
  const plotHeight = height - PAD.top - PAD.bottom;
  const base = PAD.top + plotHeight;

  // Clean ticks: a 1/2/5-decade step sized for ~4 gridlines, with the
  // ceiling rounded up to a whole step — ticks land on numbers a person
  // can hold in their head (10 / 20 / 50 …, never 12.5).
  const rawStep = peak / 4;
  const decade = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const step = [1, 2, 5, 10].map((m) => m * decade).find((s) => rawStep <= s) ?? 10 * decade;
  const ceiling = Math.ceil(peak / step) * step;
  const y = (revenue) => base - (revenue / ceiling) * plotHeight;
  const ticks = [];
  for (let t = step; t <= ceiling + step / 1000; t += step) ticks.push(t);

  const band = plotWidth / days.length;
  // Never the whole slot: at least 2px of surface stands between neighbours.
  const barWidth = Math.min(24, Math.max(2, band - 2));

  // Which days are named along the bottom: today (the last), then back a
  // week at a time — the same weekday each — or in wider steps where a week
  // of columns is too narrow for a date.
  const labelEvery = Math.max(7, Math.ceil(days.length / Math.max(2, Math.floor(plotWidth / 64))));
  const named = (i) => (days.length - 1 - i) % labelEvery === 0;
  const center = (i) => PAD.left + i * band + band / 2;

  const pick = (e) => {
    const box = e.currentTarget.getBoundingClientRect();
    const i = Math.floor(((e.clientX - box.left) / box.width) * days.length);
    setPicked(Math.min(days.length - 1, Math.max(0, i)));
  };

  const onKeyDown = (e) => {
    const last = days.length - 1;
    let next = null;
    if (e.key === "ArrowLeft") next = Math.max(0, (picked ?? days.length) - 1);
    else if (e.key === "ArrowRight") next = Math.min(last, (picked ?? -1) + 1);
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = last;
    else if (e.key === "Escape") {
      setPicked(null);
      return;
    }
    if (next === null) return;
    e.preventDefault();
    setPicked(next);
  };

  const shown = picked === null ? null : days[picked];
  const busy = days.filter((d) => d.orders > 0).reverse(); // newest first
  const total = days.reduce((sum, d) => ({ orders: sum.orders + d.orders, revenue: sum.revenue + d.revenue }), {
    orders: 0,
    revenue: 0,
  });

  return (
    <div className="mt-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs opacity-70">Order value by day</p>
        <div className="join" role="group" aria-label="Show the 30 days as">
          {VIEWS.map((v) => (
            <button
              key={v.value}
              type="button"
              className={`btn btn-xs join-item ${
                view === v.value ? "btn-secondary text-secondary-content" : "btn-ghost border border-base-300"
              }`}
              aria-pressed={view === v.value}
              onClick={() => {
                setPicked(null);
                setView(v.value);
              }}
            >
              {v.label}
            </button>
          ))}
        </div>
      </div>

      {view === "chart" ? (
        <div
          ref={setPlot}
          className="relative mt-3 rounded-lg"
          role="group"
          tabIndex={0}
          aria-label="Order value for each of the last 30 days. Use the left and right arrow keys to read a day."
          onKeyDown={onKeyDown}
          onBlur={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget)) setPicked(null);
          }}
        >
          <svg
            viewBox={`0 0 ${width} ${height}`}
            width="100%"
            height={height}
            className="block touch-pan-y"
            aria-hidden="true"
          >
            {/* Recessive gridlines + clean ticks (text in ink, never the mark colour) */}
            {ticks.map((t) => (
              <g key={t}>
                <line
                  x1={PAD.left}
                  x2={width - PAD.right}
                  y1={y(t)}
                  y2={y(t)}
                  className="stroke-base-content/10"
                  strokeWidth={1}
                />
                <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end" className="figures fill-base-content/70 text-[11px]">
                  {axisMoney(t)}
                </text>
              </g>
            ))}

            {/* The day being read, as a quiet band behind its column */}
            {picked !== null && (
              <rect
                x={PAD.left + picked * band}
                y={PAD.top}
                width={band}
                height={plotHeight}
                className="fill-base-content/[0.07]"
              />
            )}

            {days.map((day, i) => {
              const tall = base - y(day.revenue);
              const x = center(i) - barWidth / 2;
              return (
                <g key={day.date}>
                  {day.revenue > 0 && (
                    <path
                      d={column(x, y(day.revenue), barWidth, base, Math.min(4, barWidth / 2, tall))}
                      className={`fill-secondary ${picked === null || picked === i ? "" : "opacity-60"}`}
                    />
                  )}
                  {named(i) && (
                    <text
                      x={Math.min(Math.max(center(i), 22), width - 22)}
                      y={height - 8}
                      textAnchor="middle"
                      className="figures fill-base-content/70 text-[11px]"
                    >
                      {dayLabel(day)}
                    </text>
                  )}
                </g>
              );
            })}

            {/* The baseline, over the columns' feet */}
            <line
              x1={PAD.left}
              x2={width - PAD.right}
              y1={base}
              y2={base}
              className="stroke-base-content/30"
              strokeWidth={1}
            />

            {/* One target, the whole plot: the pointer only has to be over a
                day, never on its column. */}
            <rect
              x={PAD.left}
              y={PAD.top}
              width={plotWidth}
              height={plotHeight}
              fill="transparent"
              onPointerMove={pick}
              onPointerDown={pick}
              onPointerLeave={(e) => {
                if (e.pointerType === "mouse") setPicked(null);
              }}
            />
          </svg>

          {/* The day's figures: the value leads, the day follows. Always in
              the page (so a screen reader hears it change), drawn only
              while a day is being read. */}
          <div
            role="status"
            className={
              shown
                ? "pointer-events-none absolute top-0 -translate-x-1/2 rounded-box bg-base-100 px-3 py-2 text-xs whitespace-nowrap shadow-lg ring-1 ring-base-300/70"
                : "sr-only"
            }
            style={shown ? { left: Math.min(width - 76, Math.max(76, center(picked))) } : undefined}
          >
            {shown && (
              <>
                <p className="text-sm font-bold">{money(shown.revenue)}</p>
                <p className="opacity-70">
                  {dayLabel(shown, { weekday: "short", month: "short", day: "numeric" })} · {shown.orders} order
                  {shown.orders === 1 ? "" : "s"}
                </p>
              </>
            )}
          </div>
        </div>
      ) : (
        // The same 30 days as rows — every figure the chart draws, with
        // nothing to hover. Quiet days are left out; the total counts them.
        <div className="mt-3 max-h-80 overflow-y-auto overscroll-contain rounded-lg border border-base-300/70">
          <table className="table table-sm">
            <caption className="sr-only">Order value for each day with orders, newest first</caption>
            <thead className="sticky top-0 bg-base-100">
              <tr className="text-xs opacity-70">
                <th scope="col">Day</th>
                <th scope="col" className="text-right">Orders</th>
                <th scope="col" className="text-right">Order value</th>
              </tr>
            </thead>
            <tbody>
              {busy.map((day) => (
                <tr key={day.date}>
                  <td className="whitespace-nowrap">
                    {dayLabel(day, { weekday: "short", month: "short", day: "numeric" })}
                  </td>
                  <td className="figures text-right">{day.orders}</td>
                  <td className="figures text-right font-semibold">{money(day.revenue)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th scope="row" className="font-semibold">All 30 days</th>
                <td className="figures text-right font-semibold">{total.orders}</td>
                <td className="figures text-right font-bold">{money(total.revenue)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </div>
  );
}
