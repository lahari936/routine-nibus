import { useEffect, useMemo, useState } from "react";
import { auth, loadConfig, makeApi } from "./api.js";
import { HourlyChart, Timeline } from "./charts.jsx";
import { Alerts, Ask, Settings, SignIn } from "./panels.jsx";
import { addDays, clock, localMidnight, localParts, prettyDate, today, weekdayIndex } from "./time.js";

export default function App() {
  const [config, setConfig] = useState(null);
  const [signedIn, setSignedIn] = useState(!!auth.token());
  useEffect(() => { loadConfig().then(setConfig); }, []);
  if (!config) return <p className="loading">Loading…</p>;
  if (!config.demo && !signedIn) {
    return <SignIn onSubmit={async (mode, f) => {
      if (mode === "signUp") { await auth.signUp(config, f.email, f.password); return "confirm"; }
      if (mode === "confirm") await auth.confirm(config, f.email, f.code);
      await auth.signIn(config, f.email, f.password);
      setSignedIn(true);
    }} />;
  }
  return <Dashboard config={config} onSignOut={() => { auth.signOut(); setSignedIn(false); }} />;
}

function Dashboard({ config, onSignOut }) {
  const api = useMemo(() => makeApi(config), [config]);
  const [homes, setHomes] = useState(null);
  const [homeId, setHomeId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [day, setDay] = useState(null);
  const [events, setEvents] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api.homes().then((hs) => { setHomes(hs); setHomeId(hs[0]?.id ?? null); }).catch((e) => setError(e.message));
  }, [api]);

  useEffect(() => {
    if (!homeId) return;
    api.home(homeId).then((d) => {
      setDetail(d);
      setDay((current) => current ?? d.today ?? today(d.home.timezone));
    }).catch((e) => setError(e.message));
  }, [api, homeId]);

  useEffect(() => {
    if (!detail || !day) return;
    const tz = detail.home.timezone;
    const from = localMidnight(day, tz).toISOString(), to = localMidnight(addDays(day, 1), tz).toISOString();
    Promise.all([api.events(homeId, from, to), api.alerts(homeId, addDays(day, -6), day)])
      .then(([ev, al]) => { setEvents(ev); setAlerts(al); })
      .catch((e) => setError(e.message));
  }, [api, homeId, detail, day]);

  if (error) return <main className="page"><p className="error">{error}</p><button className="link" onClick={onSignOut}>Sign out</button></main>;
  if (homes && !homes.length) return <main className="page"><p>No homes list your email as a caregiver yet.</p><button className="link" onClick={onSignOut}>Sign out</button></main>;
  if (!detail || !day) return <p className="loading">Loading…</p>;

  const { home, baseline, learning, digest } = detail;
  const tz = home.timezone;
  const dayAlerts = alerts.filter((a) => localParts(new Date(a.ts), tz).date === day);
  const actual = Array(24).fill(0);
  events.filter((e) => !["offline", "online"].includes(e.kind)).forEach((e) => { actual[localParts(new Date(e.ts), tz).h]++; });
  const first = events.find((e) => e.indoor && !["offline", "online"].includes(e.kind) && localParts(new Date(e.ts), tz).h >= home.quiet_hours[1]);
  const isToday = day >= (detail.today ?? today(tz));

  return (
    <main className="page">
      <header className="top">
        <div>
          <h1>{home.name}</h1>
          <div className="badges">
            {config.demo && <span className="pill demo">Demo data</span>}
            {learning && <span className="pill learning">Learning this home's routine</span>}
            {home.paused && <span className="pill paused">Monitoring paused</span>}
          </div>
        </div>
        <nav className="day-nav" aria-label="Choose day">
          <button className="icon" aria-label="Previous day" onClick={() => setDay(addDays(day, -1))}>‹</button>
          <strong>{prettyDate(day)}</strong>
          <button className="icon" aria-label="Next day" disabled={isToday} onClick={() => setDay(addDays(day, 1))}>›</button>
        </nav>
        {!config.demo && <button className="link" onClick={onSignOut}>Sign out</button>}
      </header>

      {digest && <section className="card digest"><h2>Daily digest · {prettyDate(digest.date)}</h2><p>{digest.text}</p></section>}

      <section className="card">
        <div className="card-head">
          <h2>The day at a glance</h2>
          <p className="muted">
            First activity {first ? clock(localParts(new Date(first.ts), tz).minutes) : "not seen"}
            {baseline && ` · usually ${clock(baseline.first_activity.p50)}`} · {dayAlerts.length ? `${dayAlerts.length} alert${dayAlerts.length > 1 ? "s" : ""}` : "no alerts"}
          </p>
        </div>
        <Timeline events={events} alerts={dayAlerts} home={home} baseline={baseline} />
        <p className="legend"><span className="key band" /> usual wake-up window <span className="key quiet" /> quiet hours <span className="key ding" /> doorbell <span className="key motion" /> movement / door</p>
      </section>

      {baseline && (
        <section className="card">
          <div className="card-head">
            <h2>Activity by hour</h2>
            <p className="muted">Bars: a usual {prettyDate(day).split(",")[0]} over the last {baseline.days} days · Line: this day</p>
          </div>
          <HourlyChart usual={baseline.hourly_rate[weekdayIndex(day)]} actual={actual} />
          {baseline.recurring_visits.length > 0 && (
            <p className="muted">Regular visits: {baseline.recurring_visits.map((v) => `${v.label} (${Math.round(v.hit_rate * 100)}% of expected days)`).join(", ")}</p>
          )}
        </section>
      )}

      <div className="split">
        <section className="card"><h2>Alerts, week to {prettyDate(day)}</h2><Alerts alerts={alerts} tz={tz} /></section>
        <section className="card"><h2>Ask about the week</h2><Ask api={api} homeId={home.id} /></section>
      </div>

      <details className="card">
        <summary><h2>Settings</h2></summary>
        <Settings key={home.id} home={home} onSave={async (s) => {
          const updated = await api.saveSettings(home.id, s);
          setDetail({ ...detail, home: { ...home, ...updated } });
        }} />
      </details>

      <footer className="muted">Routine Guardian notices changes in routine from Ring motion, doorbell and door events. No video is analysed. It is not a medical device.</footer>
    </main>
  );
}
