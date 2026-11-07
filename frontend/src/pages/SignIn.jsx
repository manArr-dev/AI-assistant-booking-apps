import { useState } from "react";
import QRCode from "qrcode";
import { api } from "../api.js";
import { useAuth } from "../auth.jsx";

// Steps: password -> (mfa_setup -> recovery) | mfa_verify -> signed in.
export default function SignIn() {
  const { setUser, notice } = useAuth();
  const [mode, setMode] = useState("login"); // login | register
  const [step, setStep] = useState("password");
  const [form, setForm] = useState({ full_name: "", email: "", password: "", confirm: "" });
  const [challenge, setChallenge] = useState("");
  const [setup, setSetup] = useState(null); // { secret, uri, qr }
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function run(fn) {
    setError("");
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function next(res) {
    if (res.status === "signed_in") {
      if (res.recovery_codes) {
        setRecovery({ codes: res.recovery_codes, user: res.user });
        setStep("recovery");
      } else {
        setUser(res.user);
      }
      return;
    }
    setChallenge(res.challenge);
    setCode("");
    if (res.status === "mfa_verify") {
      setStep("mfa_verify");
    } else {
      const s = await api.post("/auth/mfa/setup", { challenge: res.challenge });
      setSetup({ ...s, qr: await QRCode.toDataURL(s.uri, { margin: 1, width: 220 }) });
      setStep("mfa_setup");
    }
  }

  const submitPassword = (e) => {
    e.preventDefault();
    run(async () => {
      if (mode === "register") {
        if (form.password !== form.confirm) throw new Error("Passwords don't match.");
        await next(await api.post("/auth/register", {
          full_name: form.full_name, email: form.email, password: form.password,
        }));
      } else {
        await next(await api.post("/auth/login", { email: form.email, password: form.password }));
      }
    });
  };

  const submitCode = (path) => (e) => {
    e.preventDefault();
    run(async () => next(await api.post(path, { challenge, code })));
  };

  const restart = () => {
    setStep("password");
    setChallenge("");
    setSetup(null);
    setCode("");
    setError("");
  };

  return (
    <div className="auth-page">
      <div className="card auth-card">
        <h1 className="auth-title">Travel Booking</h1>

        {step === "password" && (
          <>
            <div className="segmented" role="tablist">
              <button role="tab" aria-selected={mode === "login"} className={mode === "login" ? "on" : ""}
                onClick={() => { setMode("login"); setError(""); }}>Sign in</button>
              <button role="tab" aria-selected={mode === "register"} className={mode === "register" ? "on" : ""}
                onClick={() => { setMode("register"); setError(""); }}>Create account</button>
            </div>
            {notice && <p className="info">{notice}</p>}
            <form onSubmit={submitPassword} className="stack">
              {mode === "register" && (
                <label className="field">Full name
                  <input required autoComplete="name" value={form.full_name} onChange={set("full_name")} />
                </label>
              )}
              <label className="field">Email
                <input required type="email" autoComplete="email" value={form.email} onChange={set("email")} />
              </label>
              <label className="field">Password
                <input required type="password" minLength={mode === "register" ? 12 : undefined}
                  autoComplete={mode === "register" ? "new-password" : "current-password"}
                  value={form.password} onChange={set("password")} />
                {mode === "register" && <span className="hint">At least 12 characters.</span>}
              </label>
              {mode === "register" && (
                <label className="field">Repeat password
                  <input required type="password" autoComplete="new-password" value={form.confirm}
                    onChange={set("confirm")} />
                </label>
              )}
              {error && <p className="error" role="alert">{error}</p>}
              <button className="btn primary" disabled={busy}>
                {busy ? "Please wait…" : mode === "register" ? "Create account" : "Continue"}
              </button>
            </form>
          </>
        )}

        {step === "mfa_setup" && setup && (
          <form onSubmit={submitCode("/auth/mfa/confirm")} className="stack">
            <h2>Set up two-factor authentication</h2>
            <p className="muted">
              Scan this with an authenticator app (Google or Microsoft Authenticator, 1Password, Authy…),
              then enter the 6-digit code it shows.
            </p>
            <img className="qr" src={setup.qr} alt="QR code for your authenticator app" width="220" height="220" />
            <details>
              <summary>Can't scan? Enter this key instead</summary>
              <code className="secret">{setup.secret.match(/.{1,4}/g).join(" ")}</code>
            </details>
            <CodeInput value={code} onChange={setCode} />
            {error && <p className="error" role="alert">{error}</p>}
            <button className="btn primary" disabled={busy || code.length < 6}>Verify and turn on</button>
            <button type="button" className="btn ghost" onClick={restart}>Back</button>
          </form>
        )}

        {step === "recovery" && recovery && (
          <div className="stack">
            <h2>Save your recovery codes</h2>
            <p className="muted">
              Each code works once if you lose your phone. Store them somewhere safe; they won't be shown again.
            </p>
            <ul className="codes">{recovery.codes.map((c) => <li key={c}><code>{c}</code></li>)}</ul>
            <button className="btn ghost" onClick={() => navigator.clipboard?.writeText(recovery.codes.join("\n"))}>
              Copy codes
            </button>
            <button className="btn primary" onClick={() => setUser(recovery.user)}>I've saved them, continue</button>
          </div>
        )}

        {step === "mfa_verify" && (
          <form onSubmit={submitCode("/auth/mfa/verify")} className="stack">
            <h2>Two-factor authentication</h2>
            <p className="muted">Enter the 6-digit code from your authenticator app, or one of your recovery codes.</p>
            <CodeInput value={code} onChange={setCode} allowRecovery />
            {error && <p className="error" role="alert">{error}</p>}
            <button className="btn primary" disabled={busy || code.length < 6}>Sign in</button>
            <button type="button" className="btn ghost" onClick={restart}>Use a different account</button>
          </form>
        )}
      </div>
    </div>
  );
}

function CodeInput({ value, onChange, allowRecovery = false }) {
  return (
    <label className="field">Authentication code
      <input
        className="code-input"
        required
        autoFocus
        autoComplete="one-time-code"
        inputMode={allowRecovery ? "text" : "numeric"}
        maxLength={allowRecovery ? 20 : 6}
        placeholder={allowRecovery ? "123456 or abc123-def456" : "123456"}
        value={value}
        onChange={(e) => onChange(allowRecovery ? e.target.value.trim() : e.target.value.replace(/\D/g, ""))}
      />
    </label>
  );
}
