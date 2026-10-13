import { clock, localParts } from "./time.js";

const KIND_LABEL = {
  motion_human: "Person moving", motion_other: "Motion", ding: "Doorbell pressed",
  contact_open: "Door opened", contact_close: "Door closed", offline: "Device offline", online: "Device back online",
};
const TICKS = [0, 6, 12, 18, 24];

/** One day, three lanes: inside activity, doorbell, alerts. Quiet hours shaded, usual wake-up band marked. */
export function Timeline({ events, alerts, home, baseline }) {
  const W = 960, lane = 34, top = 18, H = top + lane * 3 + 22, left = 92, span = W - left - 8;
  const x = (min) => left + (min / 1440) * span;
  const [qs, qe] = home.quiet_hours;
  const quiet = qs > qe ? [[qs * 60, 1440], [0, qe * 60]] : [[qs * 60, qe * 60]];
  const lanes = [
    { label: "Inside", y: top, items: events.filter((e) => e.indoor && !["offline", "online"].includes(e.kind)) },
    { label: "Doorbell", y: top + lane, items: events.filter((e) => !e.indoor || ["offline", "online"].includes(e.kind)) },
  ];
  const fa = baseline?.first_activity;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Timeline of the day's events">
      {quiet.map(([a, b], i) => <rect key={i} x={x(a)} y={top - 6} width={x(b) - x(a)} height={lane * 3} className="quiet" />)}
      {fa && <rect x={x(fa.p10)} y={top + 4} width={Math.max(2, x(fa.p90) - x(fa.p10))} height={lane - 8} rx="4" className="band">
        <title>{`Usually up between ${clock(fa.p10)} and ${clock(fa.p90)}`}</title></rect>}
      {[...lanes, { label: "Alerts", y: top + lane * 2 }].map((l) => (
        <g key={l.label}>
          <line x1={left} x2={W - 8} y1={l.y + lane / 2} y2={l.y + lane / 2} className="lane" />
          <text x={left - 10} y={l.y + lane / 2 + 4} textAnchor="end" className="lane-label">{l.label}</text>
        </g>
      ))}
      {lanes.map((l) => l.items.map((e) => {
        const p = localParts(new Date(e.ts), home.timezone);
        return (
          <circle key={e.event_id} cx={x(p.minutes)} cy={l.y + lane / 2} r={e.kind === "ding" ? 6 : 4} className={`dot ${e.kind}`}>
            <title>{`${clock(p.minutes)} · ${KIND_LABEL[e.kind]} · ${e.device_id.replaceAll("_", " ")}`}</title>
          </circle>
        );
      }))}
      {alerts.map((a) => {
        const p = localParts(new Date(a.ts), home.timezone);
        return (
          <g key={a.rule + a.ts} transform={`translate(${x(p.minutes)},${top + lane * 2 + lane / 2})`}>
            <path d="M0,-8 L8,6 L-8,6 Z" className={`alert-mark ${a.severity}`} />
            <title>{`${clock(p.minutes)} · ${a.reason} (${a.status.replaceAll("_", " ")})`}</title>
          </g>
        );
      })}
      {TICKS.map((h) => (
        <text key={h} x={x(h * 60)} y={H - 4} textAnchor={h === 0 ? "start" : h === 24 ? "end" : "middle"} className="tick">
          {h === 24 ? "midnight" : clock(h * 60).replace(":00", "")}
        </text>
      ))}
    </svg>
  );
}

/** Events per hour: the learned usual for this weekday (bars) against the selected day (dots). */
export function HourlyChart({ usual, actual }) {
  const W = 960, H = 200, left = 36, bottom = 22, top = 10, span = W - left - 8;
  const max = Math.max(1, ...usual, ...actual);
  const bw = span / 24;
  const y = (v) => top + (H - top - bottom) * (1 - v / max);
  const yTicks = [0, Math.round(max / 2), Math.round(max)];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Events per hour, usual versus selected day">
      {yTicks.map((t) => (
        <g key={t}>
          <line x1={left} x2={W - 8} y1={y(t)} y2={y(t)} className="grid" />
          <text x={left - 6} y={y(t) + 4} textAnchor="end" className="tick">{t}</text>
        </g>
      ))}
      {usual.map((v, h) => (
        <rect key={h} x={left + h * bw + 2} y={y(v)} width={bw - 4} height={y(0) - y(v)} rx="2" className="bar">
          <title>{`${clock(h * 60)}: usually ${v.toFixed(1)} events`}</title>
        </rect>
      ))}
      <polyline points={actual.map((v, h) => `${left + h * bw + bw / 2},${y(v)}`).join(" ")} className="actual-line" />
      {actual.map((v, h) => (
        <circle key={h} cx={left + h * bw + bw / 2} cy={y(v)} r="3.5" className="actual-dot">
          <title>{`${clock(h * 60)}: ${v} events`}</title>
        </circle>
      ))}
      {[0, 6, 12, 18].map((h) => (
        <text key={h} x={left + h * bw} y={H - 6} className="tick">{clock(h * 60).replace(":00", "")}</text>
      ))}
    </svg>
  );
}
