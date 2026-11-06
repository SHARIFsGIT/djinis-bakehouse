import { useState } from "react";
import { money } from "../lib/format";

/**
 * The desk's trading chart: one column per day, the last 30, in the shop's
 * active currency. A single series needs no legend — the card's title names
 * what is plotted — and the colour never carries meaning the axis doesn't
 * already say.
 *
 * Chart grammar (kept deliberately quiet so the data is the loud thing):
 * columns cap at 24px with a rounded data-end and a square baseline,
 * hairline gridlines one step off the surface, clean compact y-ticks, text
 * in ink tokens (never the series colour), and a per-day hover that shows
 * the exact figures the column stands for.
 */
export default function TradingChart({ series }) {
  const [hovered, setHovered] = useState(null);

  const width = 720;
  const height = 240;
  const padTop = 14;
  const padBottom = 26;
  const padLeft = 40;
  const padRight = 8;
  const plotWidth = width - padLeft - padRight;
  const plotHeight = height - padTop - padBottom;

  const days = series ?? [];
  const peak = Math.max(...days.map((d) => d.revenue), 0);

  // Clean ticks: a 1/2/5-decade step sized for ~4 gridlines, with the
  // ceiling rounded up to a whole step — ticks land on numbers a person
  // can hold in their head (10 / 20 / 50 …, never 12.5).
  const rawStep = peak > 0 ? peak / 4 : 1;
  const decade = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const step = [1, 2, 5, 10]
    .map((m) => m * decade)
    .find((s) => rawStep <= s) ?? 10 * decade;
  const ceiling = peak > 0 ? Math.ceil(peak / step) * step : 0;
  const y = (revenue) =>
    padTop + plotHeight - (ceiling > 0 ? (revenue / ceiling) * plotHeight : 0);
  const band = plotWidth / Math.max(days.length, 1);
  const barWidth = Math.min(24, Math.max(3, band * 0.66));
  const radius = Math.min(4, barWidth / 2);

  const ticks = [];
  for (let t = step; t <= ceiling; t += step) ticks.push(t);
  const compact = (v) =>
    v >= 1000 ? `${Math.round(v / 100) / 10}k` : `${Math.round(v)}`;

  const labelFor = (day) => {
    const d = new Date(`${day.date}T12:00:00`);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  };

  // Which days get an x-label: the first, then roughly weekly, then the last
  // — enough to read the calendar without a ruler.
  const labelEvery = Math.max(1, Math.round(days.length / 5));

  if (peak <= 0) {
    return (
      <p className="mt-4 text-sm opacity-60">
        No sales in the window yet — the chart fills as orders land.
      </p>
    );
  }

  return (
    <div className="relative mt-4">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-auto w-full"
        role="img"
        aria-label="Daily order value over the last 30 days"
      >
        {/* Recessive gridlines + clean ticks (text in ink tokens, never the mark colour) */}
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={padLeft}
              x2={width - padRight}
              y1={y(t)}
              y2={y(t)}
              className="stroke-base-content/10"
              strokeWidth={1}
            />
            <text
              x={padLeft - 6}
              y={y(t) + 4}
              textAnchor="end"
              className="fill-base-content/45 text-[11px]"
            >
              {compact(t)}
            </text>
          </g>
        ))}

        {/* The baseline */}
        <line
          x1={padLeft}
          x2={width - padRight}
          y1={padTop + plotHeight}
          y2={padTop + plotHeight}
          className="stroke-base-content/25"
          strokeWidth={1}
        />

        {days.map((day, i) => {
          const x = padLeft + i * band + (band - barWidth) / 2;
          const columnHeight = padTop + plotHeight - y(day.revenue);
          const isHovered = hovered === i;
          return (
            <g key={day.date}>
              {day.revenue > 0 && (
                <rect
                  x={x}
                  y={y(day.revenue)}
                  width={barWidth}
                  height={columnHeight}
                  rx={radius}
                  className={`fill-primary transition-opacity ${isHovered ? "opacity-100" : "opacity-85"}`}
                />
              )}
              {/* The hit target is the whole day's band, tall as the plot —
                  easier to land on than a thin column on a quiet day. */}
              <rect
                x={padLeft + i * band}
                y={padTop}
                width={band}
                height={plotHeight}
                fill="transparent"
                onMouseEnter={() => setHovered(i)}
                onMouseLeave={() => setHovered(null)}
              />
              {(i === 0 || i === days.length - 1 || i % labelEvery === 0) && (
                <text
                  x={padLeft + i * band + band / 2}
                  y={height - 8}
                  textAnchor="middle"
                  className="fill-base-content/45 text-[11px]"
                >
                  {labelFor(day)}
                </text>
              )}
            </g>
          );
        })}
      </svg>

      {hovered !== null && days[hovered] && (
        <div
          className="pointer-events-none absolute -translate-x-1/2 rounded-box bg-base-100 px-3 py-2 text-xs shadow-lg ring-1 ring-base-300/70"
          style={{
            // Clamped so the tooltip never leaves the card at either edge.
            left: `${Math.min(
              92,
              Math.max(8, ((padLeft + hovered * band + band / 2) / width) * 100)
            )}%`,
            top: 0,
          }}
        >
          <p className="font-bold">{money(days[hovered].revenue)}</p>
          <p className="opacity-60">
            {labelFor(days[hovered])} · {days[hovered].orders} order
            {days[hovered].orders === 1 ? "" : "s"}
          </p>
        </div>
      )}
    </div>
  );
}
