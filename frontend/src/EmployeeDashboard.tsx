import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, PlaceholderField, TemplateSource, UploadAnalyzeResponse } from "./api";
import DocumentPreview from "./DocumentPreview";
import { ProcessingPanel } from "./RequestProgress";

type Props = { userName: string };

function fileKind(filename: string): "xlsx" | "docx" | "pptx" | "file" {
  const ext = (filename.split(".").pop() || "").toLowerCase();
  if (ext === "xlsx" || ext === "xls" || ext === "xlsm") return "xlsx";
  if (ext === "docx" || ext === "doc") return "docx";
  if (ext === "pptx" || ext === "ppt") return "pptx";
  return "file";
}

function prettyFileTitle(d: Record<string, unknown>): string {
  const template = String(d.template_name || "").trim();
  if (template) return template;
  const raw = String(d.document_name || d.filename || "Document");
  return raw
    .replace(/\.[a-z0-9]+$/i, "")
    .replace(/_[vV]?[\d.]+_\d{8}_\d{6}$/, "")
    .replace(/[_-]+/g, " ")
    .trim() || raw;
}

function relativeTime(iso: string): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso.slice(0, 16).replace("T", " ");
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function EyeIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M2.5 12s3.5-7 9.5-7 9.5 7 9.5 7-3.5 7-9.5 7-9.5-7-9.5-7Z"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
      <circle cx="12" cy="12" r="2.6" stroke="currentColor" strokeWidth="1.8" />
    </svg>
  );
}

function PaperclipIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M21 12.5 12.5 21a6 6 0 0 1-8.5-8.5l9-9a4 4 0 0 1 5.7 5.7l-9.2 9.1a2 2 0 1 1-2.8-2.8l8.1-8"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M12 4v11" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M7.5 11.5 12 16l4.5-4.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M5 19h14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function emptyForm(fields: PlaceholderField[]): Record<string, string> {
  const next: Record<string, string> = {};
  for (const field of fields) {
    next[field.id] = "";
    next[field.label] = "";
  }
  return next;
}

function isFormEntry(t: Pick<TemplateSource, "entry_mode" | "profile_id"> | null | undefined) {
  return t?.entry_mode === "form" || t?.profile_id === "bfl" || t?.profile_id === "sample_ppt" || t?.profile_id === "brd";
}

function HelpText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return (
    <div className="format-help">
      {parts.map((part, i) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return <strong key={i}>{part.slice(2, -2)}</strong>;
        }
        if (part.startsWith("`") && part.endsWith("`")) {
          return <code key={i}>{part.slice(1, -1)}</code>;
        }
        return <span key={i}>{part}</span>;
      })}
    </div>
  );
}

export default function EmployeeDashboard({ userName }: Props) {
  const [templates, setTemplates] = useState<TemplateSource[]>([]);
  const [templateKey, setTemplateKey] = useState("");
  const [notes, setNotes] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [form, setForm] = useState<Record<string, string>>({});
  const [fields, setFields] = useState<PlaceholderField[]>([]);
  const [analysis, setAnalysis] = useState<UploadAnalyzeResponse | null>(null);
  const [docs, setDocs] = useState<Array<Record<string, unknown>>>([]);
  const [preview, setPreview] = useState<{
    filename?: string;
    s3Key?: string;
    templateId?: string;
    title: string;
    kind?: "template" | "filled";
  } | null>(null);
  const [sawFilledPreview, setSawFilledPreview] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [showExample, setShowExample] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [chatMessages, setChatMessages] = useState<Array<{ role: string; content: string }>>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatReady, setChatReady] = useState<{ filename: string; s3Key: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const selected = useMemo(
    () => templates.find((t) => t.id === templateKey) || null,
    [templates, templateKey]
  );
  const usesForm = isFormEntry(selected);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const guided = templates.filter((t) => t.guided);
  const others = templates.filter((t) => !t.guided);

  useEffect(() => {
    void (async () => {
      try {
        const [src, generated] = await Promise.all([api.templateSources(), api.generatedDocuments()]);
        setTemplates(src.templates);
        setDocs(generated.documents);
      } catch {
        /* empty */
      }
    })();
  }, []);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages, busy]);

  async function onChooseTemplate(id: string) {
    setTemplateKey(id);
    setError("");
    setSawFilledPreview(false);
    setAnalysis(null);
    setSessionId(null);
    setChatMessages([]);
    setChatInput("");
    setChatReady(null);
    setNotes("");
    setFile(null);
    if (fileRef.current) fileRef.current.value = "";
    if (!id) {
      setFields([]);
      setForm({});
      return;
    }
    setBusy(true);
    try {
      const source = templates.find((t) => t.id === id);
      const detail = await api.getTemplate(id);
      const tmpl = detail.template;
      const nextFields = tmpl.field_config?.length ? tmpl.field_config : source?.field_config || [];
      setFields(nextFields);
      setForm(emptyForm(nextFields));
      setAnalysis({
        detected_doc_type: tmpl.name,
        summary: tmpl.description || "",
        selection_reason: source?.sample_file
          ? `Using S3 file ${source.original_filename || tmpl.original_filename || tmpl.s3_key}`
          : "Selected from S3",
        confidence: source?.guided ? 0.95 : 0.8,
        template: tmpl,
        filled_fields: {},
        missing_fields: nextFields.filter((f) => f.required).map((f) => f.label),
        preview: "",
        auto_generated: false,
        llm_provider: "bedrock",
        template_source: "s3",
        template_version: tmpl.current_version,
        s3_key: tmpl.s3_key,
        profile_id: source?.profile_id,
        format_help: source?.format_help,
        sample_notes: source?.sample_notes,
      });
      if (!isFormEntry(source)) {
        const started = await api.chat("", null, id);
        setSessionId(started.session_id);
        setChatMessages(started.messages?.length ? started.messages : [{ role: "assistant", content: started.reply }]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load template from S3");
    } finally {
      setBusy(false);
    }
  }

  async function onChatSend(e?: FormEvent) {
    e?.preventDefault();
    if (!selected) {
      setError("Choose a template first.");
      return;
    }
    const text = chatInput.trim();
    if (!text && !file) {
      setError("Type a reply, paste notes, or attach a file.");
      return;
    }
    setBusy(true);
    setError("");
    setChatReady(null);
    setChatInput("");
    const attached = file;
    setFile(null);
    if (fileRef.current) fileRef.current.value = "";
    try {
      let attachmentText = "";
      if (attached) {
        const parsed = await api.parseFile(attached);
        attachmentText = String(parsed.text || "").trim();
        if (!attachmentText && !text) {
          setError("Could not read text from that file. Try a Word, Excel, PowerPoint, or text file.");
          return;
        }
      }
      const visible = text || (attached ? `Attached ${attached.name}` : text);
      setChatMessages((prev) => [...prev, { role: "user", content: visible }]);
      const res = await api.chat(text, sessionId, selected.id, {
        attachmentText: attachmentText || undefined,
        attachmentName: attached?.name,
      });
      setSessionId(res.session_id);
      setChatMessages(
        res.messages?.length
          ? res.messages
          : [
              { role: "user", content: text || (attached ? `Attached ${attached.name}` : text) },
              { role: "assistant", content: res.reply },
            ]
      );
      if (res.generation_status === "ready" && res.generated_filename && res.s3_key) {
        setChatReady({ filename: res.generated_filename, s3Key: res.s3_key });
        setSawFilledPreview(true);
        const generated = await api.generatedDocuments();
        setDocs(generated.documents);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chat failed");
    } finally {
      setBusy(false);
    }
  }

  async function onSmartFill(e?: FormEvent) {
    e?.preventDefault();
    if (!selected) {
      setError("Choose a template first.");
      return;
    }
    if (!notes.trim() && !file) {
      setError("Paste notes (or attach a file), then Smart-fill.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const res = await api.compose({
        prompt: notes,
        text: notes,
        file,
        templateId: selected.id,
        templateSource: "s3",
        s3Key: selected.s3_key || undefined,
        autoGenerate: false,
      });
      const nextFields = res.template.field_config?.length ? res.template.field_config : fields;
      setFields(nextFields);
      const nextForm = emptyForm(nextFields);
      for (const [key, value] of Object.entries(res.filled_fields || {})) {
        nextForm[key] = value;
      }
      setForm(nextForm);
      setAnalysis(res);
      setSawFilledPreview(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Smart-fill failed");
    } finally {
      setBusy(false);
    }
  }

  async function onPreview() {
    if (!analysis?.template?.id) return;
    setBusy(true);
    setError("");
    try {
      const res = await api.generate(analysis.template.id, form);
      setSawFilledPreview(true);
      setAnalysis((prev) =>
        prev
          ? {
              ...prev,
              filename: res.filename,
              download_url: res.download_url,
              s3_key: res.s3_key,
              auto_generated: true,
              fill_mode: res.fill_mode,
              filled_fields: form,
            }
          : prev
      );
      setPreview({
        filename: res.filename,
        s3Key: res.s3_key || undefined,
        title: analysis.template.name || res.filename,
        kind: "filled",
      });
      const generated = await api.generatedDocuments();
      setDocs(generated.documents);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Preview failed");
    } finally {
      setBusy(false);
    }
  }

  async function onDownload() {
    if (!analysis?.template?.id) return;
    setBusy(true);
    setError("");
    try {
      if (analysis.filename && analysis.s3_key && sawFilledPreview) {
        await api.downloadFile(analysis.filename, analysis.s3_key);
        return;
      }
      const res = await api.generate(analysis.template.id, form);
      await api.downloadFile(res.filename, res.s3_key);
      const generated = await api.generatedDocuments();
      setDocs(generated.documents);
      setSawFilledPreview(true);
      setAnalysis((prev) =>
        prev
          ? {
              ...prev,
              filename: res.filename,
              download_url: res.download_url,
              s3_key: res.s3_key,
              auto_generated: true,
              filled_fields: form,
            }
          : prev
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Download failed");
    } finally {
      setBusy(false);
    }
  }

  const listish = (field: PlaceholderField) =>
    field.field_type === "list" || field.field_type === "text" || /items|functions|pocs|summary|action|overview/i.test(field.id);

  return (
    <>
      <div className="chat-app">
        <aside className="chat-side">
          <label className="field">
            <span>1. Choose template</span>
            <select value={templateKey} onChange={(e) => void onChooseTemplate(e.target.value)}>
              <option value="">Select a template…</option>
              {guided.length > 0 ? (
                <optgroup label="Guided templates (S3)">
                  {guided.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                      {t.current_version ? ` (v${t.current_version})` : ""}
                    </option>
                  ))}
                </optgroup>
              ) : null}
              {others.length > 0 ? (
                <optgroup label="Other S3 files">
                  {others.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                      {t.current_version ? ` (v${t.current_version})` : ""}
                    </option>
                  ))}
                </optgroup>
              ) : null}
            </select>
          </label>
          {selected ? (
            <p className="muted template-meta">
              S3: <strong>{selected.original_filename || selected.s3_key}</strong>
              {selected.sample_file ? ` · matches ${selected.sample_file}` : ""}
            </p>
          ) : (
            <p className="muted">
              Business Function List, BRD, and the Hackathon presentation use a review form. Minutes of Meeting, POC List, and other files are filled in chat.
            </p>
          )}
          {selected ? (
            <p className="muted template-meta">
              {usesForm
                ? "Paste notes, Smart-fill, then review the form before preview."
                : "Reply in chat, attach a file, or paste loose notes in one message. The bot only asks for what is still missing."}
            </p>
          ) : null}
          {selected ? (
            <div className="btn-row">
              <button
                type="button"
                className="ghost"
                onClick={() =>
                  setPreview({
                    templateId: selected.id,
                    title: `${selected.name} (blank)`,
                    kind: "template",
                  })
                }
              >
                Preview blank template
              </button>
            </div>
          ) : null}

          {docs.length > 0 ? (
            <section className="recent-files">
              <header className="recent-files-head">
                <h3>Recent files</h3>
                <span className="recent-files-count">{Math.min(docs.length, 8)}</span>
              </header>
              <ul>
                {docs.slice(0, 8).map((d) => {
                  const filename = String(d.filename || "");
                  const title = prettyFileTitle(d);
                  const kind = fileKind(filename);
                  return (
                    <li key={String(d.id)} className="recent-file">
                      <span className={`file-badge file-badge-${kind}`} aria-hidden>
                        {kind}
                      </span>
                      <div className="recent-file-body">
                        <p className="recent-file-title" title={filename}>
                          {title}
                        </p>
                        <p className="recent-file-meta">
                          {relativeTime(String(d.created_at || d.modified_at || ""))}
                          {d.template_version ? ` · v${String(d.template_version)}` : ""}
                        </p>
                      </div>
                      <div className="recent-file-actions">
                        <button
                          type="button"
                          className="file-action"
                          title="Preview"
                          aria-label={`Preview ${title}`}
                          onClick={() =>
                            setPreview({
                              filename,
                              s3Key: String(d.s3_key || ""),
                              title,
                              kind: "filled",
                            })
                          }
                        >
                          <EyeIcon />
                        </button>
                        <button
                          type="button"
                          className="file-action file-action-primary"
                          title="Download"
                          aria-label={`Download ${title}`}
                          onClick={() => void api.downloadFile(filename, String(d.s3_key || "") || undefined)}
                        >
                          <DownloadIcon />
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </section>
          ) : null}
        </aside>

        <section className="chat-main wizard-main">
          <div className="chat-scroll">
            {!selected ? (
              <article className="bubble assistant">
                <p className="typed-copy">
                  Choose a template on the left. Business Function List, BRD, and the Hackathon /
                  Salesforce presentation use paste-notes plus a review form (you can attach a file).
                  Minutes of Meeting, POC List, and other templates are filled in this chat.
                </p>
              </article>
            ) : usesForm ? (
              <>
                <article className="bubble assistant">
                  <p className="typed-copy">
                    <strong>{selected.name}</strong>
                    {selected.description ? ` — ${selected.description}` : ""}
                  </p>
                  {analysis?.selection_reason ? <p className="muted">{analysis.selection_reason}</p> : null}
                </article>

                <section className="wizard-card">
                  <header className="wizard-card-head">
                    <h3>2. Paste notes</h3>
                    <div className="btn-row">
                      <button type="button" className="ghost" onClick={() => setShowHelp((v) => !v)}>
                        {showHelp ? "Hide format" : "Note format"}
                      </button>
                      <button type="button" className="ghost" onClick={() => setShowExample((v) => !v)}>
                        {showExample ? "Hide example" : "Example notes"}
                      </button>
                    </div>
                  </header>
                  {showHelp && selected.format_help ? <HelpText text={selected.format_help} /> : null}
                  {showExample && selected.sample_notes ? (
                    <div className="example-notes-wrap">
                      <pre className="example-notes">{selected.sample_notes}</pre>
                      <button
                        type="button"
                        className="ghost"
                        onClick={() => {
                          setNotes(selected.sample_notes || "");
                          setShowExample(true);
                        }}
                      >
                        Use these notes
                      </button>
                    </div>
                  ) : null}
                  <form className="notes-form" onSubmit={(e) => void onSmartFill(e)}>
                    <textarea
                      rows={8}
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      placeholder="Paste email, bullets, or loose notes — or attach a file. Labeled fields are optional."
                      disabled={busy}
                    />
                    <div className="composer-actions">
                      <input
                        ref={fileRef}
                        type="file"
                        hidden
                        accept=".txt,.md,.csv,.docx,.xlsx,.pptx,.doc,.xls,.ppt"
                        onChange={(e) => setFile(e.target.files?.[0] || null)}
                      />
                      <button
                        type="button"
                        className={`icon-btn${file ? " attached" : ""}`}
                        title={file ? file.name : "Attach a file for context"}
                        aria-label="Attach file"
                        onClick={() => fileRef.current?.click()}
                      >
                        <PaperclipIcon />
                      </button>
                      {file ? (
                        <span className="attach-name" title={file.name}>
                          {file.name}
                          <button type="button" className="ghost attach-remove" onClick={() => setFile(null)}>
                            Remove
                          </button>
                        </span>
                      ) : (
                        <span className="muted">Attach notes file</span>
                      )}
                      <button type="submit" className="primary" disabled={busy || (!notes.trim() && !file)}>
                        Smart-fill form
                      </button>
                    </div>
                  </form>
                </section>

                {fields.length > 0 ? (
                  <section className="wizard-card">
                    <header className="wizard-card-head">
                      <h3>3. Review form</h3>
                      {analysis?.missing_fields?.length ? (
                        <span className="muted">Still needed: {analysis.missing_fields.join(", ")}</span>
                      ) : (
                        <span className="muted">Edit anything before preview / download</span>
                      )}
                    </header>
                    {analysis?.kb?.used ? (
                      <p className="muted">
                        Gaps filled from S3 knowledge base
                        {analysis.kb.process ? ` (${analysis.kb.process})` : ""}
                        {analysis.kb.filled?.length ? ` — ${analysis.kb.filled.join(", ")}` : ""}.
                      </p>
                    ) : null}
                    <div className="chat-form">
                      {fields.map((field) => (
                        <label className="field" key={field.id}>
                          <span>
                            {field.label}
                            {field.required ? " *" : " — optional"}
                          </span>
                          {listish(field) ? (
                            <textarea
                              rows={field.id === "what_you_built" || field.id === "what_breaks" || field.id === "items" ? 6 : field.field_type === "list" ? 5 : 3}
                              value={form[field.id] ?? form[field.label] ?? ""}
                              onChange={(e) =>
                                setForm((prev) => ({
                                  ...prev,
                                  [field.id]: e.target.value,
                                  [field.label]: e.target.value,
                                }))
                              }
                              placeholder={field.help || field.question}
                              disabled={busy}
                            />
                          ) : (
                            <input
                              value={form[field.id] ?? form[field.label] ?? ""}
                              onChange={(e) =>
                                setForm((prev) => ({
                                  ...prev,
                                  [field.id]: e.target.value,
                                  [field.label]: e.target.value,
                                }))
                              }
                              placeholder={field.question}
                              disabled={busy}
                            />
                          )}
                          {field.help ? <small>{field.help}</small> : null}
                        </label>
                      ))}
                      <div className="btn-row">
                        <button type="button" className="ghost" disabled={busy} onClick={() => void onPreview()}>
                          Preview filled document
                        </button>
                        <button
                          type="button"
                          className="primary"
                          disabled={busy || !sawFilledPreview}
                          onClick={() => void onDownload()}
                        >
                          Download
                        </button>
                      </div>
                      {!sawFilledPreview ? (
                        <p className="muted">Preview the filled Office file before download.</p>
                      ) : null}
                    </div>
                  </section>
                ) : null}
              </>
            ) : (
              <>
                <div className="btn-row">
                  <button type="button" className="ghost" onClick={() => setShowHelp((v) => !v)}>
                    {showHelp ? "Hide format" : "Note format"}
                  </button>
                  <button type="button" className="ghost" onClick={() => setShowExample((v) => !v)}>
                    {showExample ? "Hide example" : "Example notes"}
                  </button>
                </div>
                {showHelp && selected.format_help ? <HelpText text={selected.format_help} /> : null}
                {showExample && selected.sample_notes ? (
                  <div className="example-notes-wrap">
                    <pre className="example-notes">{selected.sample_notes}</pre>
                    <button
                      type="button"
                      className="ghost"
                      onClick={() => {
                        setChatInput(selected.sample_notes || "");
                        setShowExample(true);
                      }}
                    >
                      Use these notes
                    </button>
                  </div>
                ) : null}
                {chatMessages.map((msg, i) => (
                  <article className={`bubble ${msg.role === "user" ? "user" : "assistant"}`} key={`${msg.role}-${i}`}>
                    {msg.role === "assistant" ? (
                      <HelpText text={msg.content} />
                    ) : (
                      <p className="typed-copy">{msg.content}</p>
                    )}
                  </article>
                ))}
                {chatReady ? (
                  <section className="wizard-card">
                    <header className="wizard-card-head">
                      <h3>Document ready</h3>
                      <span className="muted">{chatReady.filename}</span>
                    </header>
                    <div className="btn-row">
                      <button
                        type="button"
                        className="ghost"
                        disabled={busy}
                        onClick={() =>
                          setPreview({
                            filename: chatReady.filename,
                            s3Key: chatReady.s3Key,
                            title: selected.name,
                            kind: "filled",
                          })
                        }
                      >
                        Preview filled document
                      </button>
                      <button
                        type="button"
                        className="primary"
                        disabled={busy}
                        onClick={() => void api.downloadFile(chatReady.filename, chatReady.s3Key)}
                      >
                        Download
                      </button>
                    </div>
                  </section>
                ) : null}
                <div ref={chatEndRef} />
              </>
            )}
            {error ? (
              <article className="bubble assistant">
                <p className="typed-copy">{error}</p>
              </article>
            ) : null}
            {busy ? (
              <div className="chat-processing">
                <ProcessingPanel label="Working on your request…" />
              </div>
            ) : null}
          </div>
          {!usesForm && selected ? (
            <form className="composer" onSubmit={(e) => void onChatSend(e)}>
              <input
                ref={fileRef}
                type="file"
                hidden
                accept=".txt,.md,.csv,.docx,.xlsx,.pptx,.doc,.xls,.ppt"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
              <button
                type="button"
                className={`icon-btn${file ? " attached" : ""}`}
                title={file ? file.name : "Attach a file so placeholders fill from its content"}
                aria-label="Attach file"
                disabled={busy}
                onClick={() => fileRef.current?.click()}
              >
                <PaperclipIcon />
              </button>
              <textarea
                rows={3}
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder={file ? `Attached ${file.name} — add a note or send` : "Paste loose notes, attach a file, or type an answer…"}
                disabled={busy}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void onChatSend();
                  }
                }}
              />
              <button type="submit" className="primary" disabled={busy || (!chatInput.trim() && !file)}>
                Send
              </button>
              {file ? (
                <p className="composer-file">
                  <span title={file.name}>{file.name}</span>
                  <button
                    type="button"
                    className="ghost attach-remove"
                    onClick={() => {
                      setFile(null);
                      if (fileRef.current) fileRef.current.value = "";
                    }}
                  >
                    Remove
                  </button>
                </p>
              ) : null}
            </form>
          ) : null}
          <p className="muted user-foot">Signed in as {userName}</p>
        </section>
      </div>
      {preview ? (
        <DocumentPreview
          filename={preview.filename}
          s3Key={preview.s3Key}
          templateId={preview.templateId}
          title={preview.title}
          onClose={() => setPreview(null)}
        />
      ) : null}
    </>
  );
}
