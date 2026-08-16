import { useEffect, useState } from "react";
import { api } from "./api";
import { ProcessingPanel } from "./RequestProgress";

type Props = {
  filename?: string;
  s3Key?: string;
  templateId?: string;
  title?: string;
  onClose: () => void;
};

export default function DocumentPreview({ filename, s3Key, templateId, title, onClose }: Props) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    setBusy(true);
    setError(null);
    setUrl(null);
    const load = templateId ? api.previewTemplate(templateId) : api.previewPdf(filename || "document", s3Key);
    void load
      .then((blob) => {
        const next = URL.createObjectURL(blob);
        if (cancelled) {
          URL.revokeObjectURL(next);
          return;
        }
        objectUrl = next;
        setUrl(next);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Preview failed");
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [filename, s3Key, templateId]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="preview-overlay" role="dialog" aria-modal="true" aria-label="Document preview">
      <div className="preview-shell">
        <header className="preview-toolbar">
          <div>
            <p className="preview-kicker">Office preview</p>
            <h2>{title || filename}</h2>
          </div>
          <button type="button" className="primary" onClick={onClose}>
            Close preview
          </button>
        </header>
        <div className="preview-canvas preview-canvas-pdf">
          {busy ? <ProcessingPanel label="Rendering the Office document…" /> : null}
          {error ? <p className="error">{error}</p> : null}
          {!busy && !error && url ? (
            <iframe
              className="preview-frame"
              title={title || filename}
              src={`${url}#toolbar=1&navpanes=0&scrollbar=1`}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}
