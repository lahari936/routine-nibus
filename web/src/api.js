// Backend access. With /config.json (written at deploy) we talk to API Gateway with a Cognito ID token.
// Without it we run in demo mode on /demo.json (exported from the simulator), read-only apart from local edits.

const TOKEN_KEY = "rg.idToken";
const store = {
  get: () => { try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; } },
  set: (t) => { try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ } },
};

export async function loadConfig() {
  const res = await fetch("/config.json").catch(() => null);
  if (res?.ok && res.headers.get("content-type")?.includes("json")) return { ...(await res.json()), demo: false };
  return { demo: true };
}

async function cognito(config, target, body) {
  const res = await fetch(`https://cognito-idp.${config.region}.amazonaws.com/`, {
    method: "POST",
    headers: { "Content-Type": "application/x-amz-json-1.1", "X-Amz-Target": `AWSCognitoIdentityProviderService.${target}` },
    body: JSON.stringify({ ClientId: config.clientId, ...body }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.message || data.__type || "Sign-in failed");
  return data;
}

export const auth = {
  token: store.get,
  async signIn(config, email, password) {
    const r = await cognito(config, "InitiateAuth", { AuthFlow: "USER_PASSWORD_AUTH", AuthParameters: { USERNAME: email, PASSWORD: password } });
    store.set(r.AuthenticationResult.IdToken); // ID token: the API reads the caregiver's email claim
  },
  signUp: (config, email, password) =>
    cognito(config, "SignUp", { Username: email, Password: password, UserAttributes: [{ Name: "email", Value: email }] }),
  confirm: (config, email, code) => cognito(config, "ConfirmSignUp", { Username: email, ConfirmationCode: code }),
  signOut: () => store.set(null),
};

export function makeApi(config) {
  if (config.demo) return demoApi();
  const call = async (method, path, body) => {
    const res = await fetch(config.apiUrl + path, {
      method,
      headers: { Authorization: `Bearer ${auth.token()}`, "Content-Type": "application/json" },
      body: body && JSON.stringify(body),
    });
    if (res.status === 401) { auth.signOut(); throw new Error("Session expired, please sign in again."); }
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
    return data;
  };
  const q = (params) => new URLSearchParams(params).toString();
  return {
    homes: () => call("GET", "/api/homes"),
    home: (id) => call("GET", `/api/homes/${id}`),
    events: (id, from, to) => call("GET", `/api/homes/${id}/events?${q({ from, to })}`),
    alerts: (id, from, to) => call("GET", `/api/homes/${id}/alerts?${q({ from, to })}`),
    saveSettings: (id, settings) => call("PUT", `/api/homes/${id}/settings`, settings),
    ask: (id, question) => call("POST", `/api/homes/${id}/ask`, { question }),
  };
}

function demoApi() {
  const data = fetch("/demo.json").then((r) => r.json());
  return {
    homes: async () => [(await data).home],
    home: async () => { const d = await data; return { home: d.home, baseline: d.baseline, learning: d.baseline.days < 7, digest: d.digest, today: d.today }; },
    events: async (_, from, to) => (await data).events.filter((e) => e.ts >= from && e.ts < to),
    alerts: async (_, from, to) => (await data).alerts.filter((a) => a.ts.slice(0, 10) >= from && a.ts.slice(0, 10) <= to),
    saveSettings: async (_, settings) => Object.assign((await data).home, settings),
    ask: async () => ({ answer: "Demo mode: the caregiver agent runs on Amazon Bedrock once the stack is deployed. Try `python -m agent.caregiver_agent` locally with AWS credentials." }),
  };
}
