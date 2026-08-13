import { FormEvent, useState } from "react";
import { api, AuthUser } from "./api";
import { saveSession } from "./session";

type Props = {
  onAuthenticated: (user: AuthUser) => void;
};

export default function AuthScreen({ onAuthenticated }: Props) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("consultant@ymsli.com");
  const [password, setPassword] = useState("demo123");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res =
        mode === "login"
          ? await api.login(email, password)
          : await api.register(email, password, name || email.split("@")[0]);
      saveSession(res.access_token, res.user);
      onAuthenticated(res.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page auth-page">
      <div className="glow" aria-hidden />
      <header className="hero">
        <p className="logo">YMSLI</p>
        <h1>Template Hub</h1>
        <p className="tagline">Sign in with email and password to continue.</p>
      </header>

      <form className="card auth-card" onSubmit={(e) => void onSubmit(e)}>
        <div className="auth-tabs">
          <button
            type="button"
            className={mode === "login" ? "tab active" : "tab"}
            onClick={() => setMode("login")}
          >
            Sign in
          </button>
          <button
            type="button"
            className={mode === "register" ? "tab active" : "tab"}
            onClick={() => setMode("register")}
          >
            Register
          </button>
        </div>

        {mode === "register" ? (
          <label className="field">
            <span>Name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" disabled={busy} />
          </label>
        ) : null}

        <label className="field">
          <span>Email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@ymsli.com"
            required
            disabled={busy}
          />
        </label>

        <label className="field">
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Min 6 characters"
            required
            minLength={6}
            disabled={busy}
          />
        </label>

        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
        </button>

        {error ? <p className="error">{error}</p> : null}

        <p className="muted demo-hint">
          Demo: <code>consultant@ymsli.com</code> / <code>demo123</code>
        </p>
      </form>
    </div>
  );
}
