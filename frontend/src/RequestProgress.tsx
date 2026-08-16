import { useEffect, useState } from "react";
import { subscribeRequestActivity } from "./api";

const SHOW_AFTER_MS = 160;

export default function RequestProgress() {
  const [pending, setPending] = useState(0);
  const [visible, setVisible] = useState(false);
  const [finishing, setFinishing] = useState(false);

  useEffect(() => subscribeRequestActivity(setPending), []);

  useEffect(() => {
    if (pending > 0) {
      setFinishing(false);
      const show = window.setTimeout(() => setVisible(true), SHOW_AFTER_MS);
      return () => window.clearTimeout(show);
    }
    if (!visible) return;
    setFinishing(true);
    const hide = window.setTimeout(() => {
      setVisible(false);
      setFinishing(false);
    }, 280);
    return () => window.clearTimeout(hide);
  }, [pending, visible]);

  if (!visible && !finishing) return null;

  return (
    <div
      className={`request-progress${visible ? " visible" : ""}${finishing ? " finishing" : pending > 0 ? " active" : ""}`}
      role="status"
      aria-live="polite"
      aria-label="Processing request"
    >
      <div className="request-progress-track">
        <div className="request-progress-bar" />
      </div>
      <p className="request-progress-label">
        <span className="request-progress-pulse" aria-hidden />
        Processing…
      </p>
    </div>
  );
}

export function ProcessingPanel({ label = "Processing…" }: { label?: string }) {
  return (
    <div className="processing-panel" role="status" aria-live="polite">
      <div className="processing-panel-track">
        <div className="processing-panel-bar" />
      </div>
      <p>{label}</p>
    </div>
  );
}
