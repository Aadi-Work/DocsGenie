function friendlyMessage(raw: string): string {
  const text = (raw || "").trim();
  if (!text) return "Something went wrong.";
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
    if (Array.isArray(parsed.detail)) {
      return parsed.detail
        .map((item) => (typeof item === "object" && item && "msg" in item ? String((item as { msg: string }).msg) : String(item)))
        .join(" ");
    }
  } catch {
    /* not JSON */
  }
  return text.replace(/^Error:\s*/i, "");
}

type Props = {
  title?: string;
  message: string;
  kind?: "error" | "info";
  onClose: () => void;
};

export default function Popup({ title, message, kind = "error", onClose }: Props) {
  return (
    <div className="popup-backdrop" role="alertdialog" aria-modal="true" onClick={onClose}>
      <div className={`popup-card popup-${kind}`} onClick={(e) => e.stopPropagation()}>
        <h3>{title || (kind === "error" ? "Error" : "Message")}</h3>
        <p>{friendlyMessage(message)}</p>
        <button type="button" className="primary" onClick={onClose}>
          OK
        </button>
      </div>
    </div>
  );
}

export { friendlyMessage };
