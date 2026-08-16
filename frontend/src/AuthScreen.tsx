import { FormEvent, useState } from "react";
import { api, AuthUser } from "./api";
import Popup from "./Popup";
import { ProcessingPanel } from "./RequestProgress";
import { saveSession } from "./session";

type Props = { onAuthenticated: (user: AuthUser) => void };

export default function AuthScreen({ onAuthenticated }: Props) {
  const [userId, setUserId] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [popup, setPopup] = useState<{ kind: "error" | "info"; message: string } | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await api.login(userId.trim(), password);
      saveSession(res.access_token, res.user);
      onAuthenticated(res.user);
    } catch (err) {
      setPopup({
        kind: "error",
        message: err instanceof Error ? err.message : "Sign in failed",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page auth-page">
      <div className="glow" aria-hidden />
      <header className="hero auth-hero">
        <p className="logo">YMSLI</p>
        <h1>Template Hub</h1>
      </header>

      <form className="card auth-card" onSubmit={(e) => void onSubmit(e)}>
        <h2 className="auth-title">Sign-In</h2>

        <label className="field">
          <span>User ID</span>
          <input
            type="text"
            autoComplete="username"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            required
            disabled={busy}
          />
        </label>

        <label className="field">
          <span>Password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={6}
            disabled={busy}
          />
        </label>

        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Please wait…" : "Sign in"}
        </button>
        {busy ? <ProcessingPanel label="Signing in…" /> : null}
      </form>

      {popup ? (
        <Popup kind={popup.kind} message={popup.message} onClose={() => setPopup(null)} />
      ) : null}
    </div>
  );
}
