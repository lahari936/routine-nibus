import { useState } from "react";
import { prettyDate } from "./time.js";

const STATUS = { open: "Waiting for a caregiver", acked: "Acknowledged", self_checked_in: "Checked in at the doorbell", unresolved: "No one responded" };

export function Alerts({ alerts, tz }) {
  if (!alerts.length) return <p className="muted">No alerts this week.</p>;
  return (
    <ul className="alerts">
      {[...alerts].reverse().map((a) => (
        <li key={a.rule + a.ts} className={`alert ${a.severity}`}>
          <div className="alert-head">
            <span>{prettyDate(new Date(a.ts).toLocaleDateString("en-CA", { timeZone: tz }))}</span>
            <span className={`pill ${a.status}`}>{STATUS[a.status]}{a.acked_by ? ` · ${a.acked_by}` : ""}</span>
          </div>
          <p>{a.reason}</p>
        </li>
      ))}
    </ul>
  );
}

export function Ask({ api, homeId }) {
  const [question, setQuestion] = useState("How was Mom's week?");
  const [answer, setAnswer] = useState(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setAnswer(null);
    try { setAnswer((await api.ask(homeId, question)).answer); }
    catch (err) { setAnswer(`Sorry, that didn't work: ${err.message}`); }
    finally { setBusy(false); }
  };
  return (
    <form onSubmit={submit} className="ask">
      <label htmlFor="question" className="sr-only">Question</label>
      <input id="question" value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={500} />
      <button disabled={busy || !question.trim()}>{busy ? "Thinking…" : "Ask"}</button>
      {answer && <p className="answer">{answer}</p>}
    </form>
  );
}

export function Settings({ home, onSave }) {
  const [draft, setDraft] = useState(home);
  const [status, setStatus] = useState("");
  const set = (k, v) => setDraft({ ...draft, [k]: v });
  const setCaregiver = (i, k, v) => set("caregivers", draft.caregivers.map((c, j) => (j === i ? { ...c, [k]: v || null } : c)));
  const save = async (e) => {
    e.preventDefault();
    setStatus("Saving…");
    try {
      const { name, timezone, quiet_hours, checkin_enabled, snapshot_optin, paused, caregivers } = draft;
      await onSave({ name, timezone, quiet_hours, checkin_enabled, snapshot_optin, paused, caregivers });
      setStatus("Saved");
    } catch (err) { setStatus(err.message); }
  };
  const hours = [...Array(24).keys()];
  return (
    <form onSubmit={save} className="settings">
      <label>Home name<input value={draft.name} onChange={(e) => set("name", e.target.value)} /></label>
      <label>Time zone<input value={draft.timezone} onChange={(e) => set("timezone", e.target.value)} /></label>
      <fieldset className="row">
        <legend>Quiet hours</legend>
        <select aria-label="Quiet hours start" value={draft.quiet_hours[0]} onChange={(e) => set("quiet_hours", [+e.target.value, draft.quiet_hours[1]])}>
          {hours.map((h) => <option key={h} value={h}>{`${h}:00`}</option>)}
        </select>
        <span>to</span>
        <select aria-label="Quiet hours end" value={draft.quiet_hours[1]} onChange={(e) => set("quiet_hours", [draft.quiet_hours[0], +e.target.value])}>
          {hours.map((h) => <option key={h} value={h}>{`${h}:00`}</option>)}
        </select>
      </fieldset>
      <label className="check"><input type="checkbox" checked={draft.paused} onChange={(e) => set("paused", e.target.checked)} />
        Pause monitoring (vacation)</label>
      <label className="check"><input type="checkbox" checked={draft.checkin_enabled} onChange={(e) => set("checkin_enabled", e.target.checked)} />
        Before alerting family, ask at home: “press your doorbell if you're OK”</label>
      <label className="check"><input type="checkbox" checked={draft.snapshot_optin} onChange={(e) => set("snapshot_optin", e.target.checked)} />
        Attach a doorbell snapshot link to alerts (only while an alert is active; the link expires)</label>
      <fieldset>
        <legend>Caregivers, in the order they're contacted</legend>
        {draft.caregivers.map((c, i) => (
          <div key={i} className="caregiver">
            <input aria-label="Name" placeholder="Name" value={c.name} onChange={(e) => setCaregiver(i, "name", e.target.value)} required />
            <input aria-label="Email" type="email" placeholder="Email" value={c.email || ""} onChange={(e) => setCaregiver(i, "email", e.target.value)} />
            <input aria-label="Phone" type="tel" placeholder="+1 555 123 4567" value={c.phone || ""} onChange={(e) => setCaregiver(i, "phone", e.target.value)} />
            <button type="button" className="link" onClick={() => set("caregivers", draft.caregivers.filter((_, j) => j !== i))}>Remove</button>
          </div>
        ))}
        <button type="button" className="link" onClick={() => set("caregivers", [...draft.caregivers, { name: "", email: null, phone: null }])}>+ Add caregiver</button>
      </fieldset>
      <div className="row"><button>Save settings</button><span className="muted" role="status">{status}</span></div>
    </form>
  );
}

export function SignIn({ onSubmit }) {
  const [mode, setMode] = useState("signIn");
  const [form, setForm] = useState({ email: "", password: "", code: "" });
  const [error, setError] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    setError("");
    try { setMode((await onSubmit(mode, form)) || mode); } catch (err) { setError(err.message); }
  };
  const field = (k, type, label) => (
    <label>{label}<input type={type} required value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} /></label>
  );
  return (
    <main className="signin">
      <h1>Routine Guardian</h1>
      <p className="muted">Sign in with the email the home's owner added as a caregiver.</p>
      <form onSubmit={submit}>
        {field("email", "email", "Email")}
        {mode !== "confirm" && field("password", "password", "Password")}
        {mode === "confirm" && field("code", "text", "Code from your email")}
        {error && <p className="error">{error}</p>}
        <button>{{ signIn: "Sign in", signUp: "Create account", confirm: "Confirm" }[mode]}</button>
      </form>
      {mode === "signIn" && <button className="link" onClick={() => setMode("signUp")}>New here? Create an account</button>}
      {mode === "signUp" && <button className="link" onClick={() => setMode("signIn")}>Have an account? Sign in</button>}
    </main>
  );
}
