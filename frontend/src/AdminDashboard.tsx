import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, DiffLine, PlaceholderField, TemplateMeta, VersionCompare, filenameFromUrl } from "./api";
import DocumentPreview from "./DocumentPreview";
import Popup from "./Popup";
import { ProcessingPanel } from "./RequestProgress";
import S3AnalyticsPanel from "./S3AnalyticsPanel";

type Props = { userName: string };
type Tab = "templates" | "upload" | "history" | "documents" | "analytics";

function templateKind(t: TemplateMeta): "xlsx" | "docx" | "pptx" | "file" {
  const raw = `${t.original_filename || ""} ${t.output_format || ""} ${t.category || ""}`.toLowerCase();
  if (raw.includes("xls") || raw.includes("excel")) return "xlsx";
  if (raw.includes("doc") || raw.includes("word")) return "docx";
  if (raw.includes("ppt") || raw.includes("power")) return "pptx";
  return "file";
}

function fmt(ts?: string | null) {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts).slice(0, 16).replace("T", " ");
  return d.toLocaleString();
}

function DiffView({ lines }: { lines: DiffLine[] }) {
  if (!lines.length) {
    return <p className="muted">No text or cell differences to show.</p>;
  }
  return (
    <pre className="diff-view">
      {lines.map((l, i) => (
        <div key={i} className={`diff-line diff-${l.type}`}>
          <span className="diff-mark">{l.type === "added" ? "+" : l.type === "removed" ? "-" : " "}</span>
          <span>{l.text}</span>
        </div>
      ))}
    </pre>
  );
}

export default function AdminDashboard({ userName }: Props) {
  const [tab, setTab] = useState<Tab>("templates");
  const [templates, setTemplates] = useState<TemplateMeta[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [versions, setVersions] = useState<TemplateMeta["versions"]>([]);
  const [docs, setDocs] = useState<Array<Record<string, unknown>>>([]);
  const [fromVer, setFromVer] = useState("");
  const [toVer, setToVer] = useState("");
  const [compare, setCompare] = useState<VersionCompare | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [description, setDescription] = useState("");
  const [placeholders, setPlaceholders] = useState("");
  const [outline, setOutline] = useState("");
  const [questions, setQuestions] = useState("");
  const [changelog, setChangelog] = useState("");
  const [fields, setFields] = useState<PlaceholderField[]>([]);
  const [editFile, setEditFile] = useState<File | null>(null);

  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadName, setUploadName] = useState("");
  const [uploadDesc, setUploadDesc] = useState("");
  const [analyzed, setAnalyzed] = useState(false);
  const [historySearch, setHistorySearch] = useState("");
  const [preview, setPreview] = useState<{ filename: string; s3Key?: string; title: string } | null>(null);
  const [listQuery, setListQuery] = useState("");
  const [panelReady, setPanelReady] = useState(false);

  const selected = templates.find((t) => t.id === selectedId) || null;
  const visibleTemplates = useMemo(() => {
    const q = listQuery.trim().toLowerCase();
    if (!q) return templates;
    return templates.filter((t) =>
      [t.name, t.current_version, t.current_status, t.output_format, t.original_filename, t.category]
        .join(" ")
        .toLowerCase()
        .includes(q)
    );
  }, [templates, listQuery]);

  async function reload() {
    const [res, generated] = await Promise.all([api.listTemplates(), api.generatedDocuments()]);
    setTemplates(res.templates);
    setDocs(generated.documents);
  }

  useEffect(() => {
    void reload().catch((e) => setError(e instanceof Error ? e.message : "Failed to load"));
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setVersions([]);
      setCompare(null);
      setFields([]);
      setPanelReady(false);
      return;
    }
    let cancelled = false;
    setPanelReady(false);
    setFields([]);
    setChangelog("");
    setEditFile(null);
    void (async () => {
      try {
        const [v, detail] = await Promise.all([api.templateVersions(selectedId), api.getTemplate(selectedId)]);
        if (cancelled) return;
        setVersions(v.versions);
        const t = detail.template;
        const placeholders = t.placeholders || [];
        const fieldConfig = (t.field_config || []).filter((f) => (f.label || f.question || "").trim());
        setDescription(t.description || "");
        setPlaceholders(placeholders.join("\n"));
        setOutline((t.content_outline || []).join("\n"));
        setQuestions((t.context_questions || []).join("\n"));
        setFields(
          fieldConfig.length
            ? fieldConfig
            : placeholders.map((p) => ({
                id: p.toLowerCase().replace(/[^a-z0-9]+/g, "_"),
                label: p,
                question: `What is the ${p}?`,
                required: true,
                field_type: "string",
                source: "detected",
              }))
        );
        if (v.versions.length >= 2) {
          setToVer(v.versions[0].version);
          setFromVer(v.versions[1].version);
        } else if (v.versions[0]) {
          setFromVer(v.versions[0].version);
          setToVer(v.versions[0].version);
        }
        setPanelReady(true);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load versions");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);

  async function saveVersion(e: FormEvent) {
    e.preventDefault();
    if (!selectedId || !changelog.trim()) {
      setError("Change description is required");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.adminSaveVersion(selectedId, {
        changelog: changelog.trim(),
        description,
        placeholders: lines(placeholders),
        questions: lines(questions),
        outline: lines(outline),
        fieldConfig: fields,
        file: editFile,
      });
      setChangelog("");
      setEditFile(null);
      setNotice("New version saved to S3 and marked active.");
      await reload();
      const v = await api.templateVersions(selectedId);
      setVersions(v.versions);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function previewExisting() {
    if (!selectedId) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.adminPreview({
        templateId: selectedId,
        notes: description,
        answers: Object.fromEntries(fields.map((f) => [f.label, `[${f.label}]`])),
      });
      setPreview({
        filename: res.filename || filenameFromUrl(res.download_url),
        s3Key: res.s3_key || undefined,
        title: selected?.name || res.filename,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Preview failed");
    } finally {
      setBusy(false);
    }
  }

  async function onAnalyzeUpload() {
    if (!uploadFile) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.adminAnalyze(uploadFile);
      setUploadName(uploadFile.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " "));
      setUploadDesc(res.preview_text.slice(0, 240));
      setPlaceholders(res.placeholders.join("\n"));
      setQuestions(res.context_questions.join("\n"));
      setOutline(res.content_outline.join("\n"));
      setFields(res.field_config);
      setAnalyzed(true);
      setNotice(res.message);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analyze failed");
    } finally {
      setBusy(false);
    }
  }

  async function onPreviewUpload() {
    if (!uploadFile) return;
    setBusy(true);
    try {
      const res = await api.adminPreview({
        file: uploadFile,
        notes: uploadDesc,
        answers: Object.fromEntries(fields.map((f) => [f.label, `[${f.label}]`])),
      });
      setPreview({
        filename: res.filename || filenameFromUrl(res.download_url),
        s3Key: res.s3_key || undefined,
        title: uploadName || res.filename,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Preview failed");
    } finally {
      setBusy(false);
    }
  }

  async function onSaveUpload() {
    if (!uploadFile) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.adminUpload({
        file: uploadFile,
        name: uploadName,
        description: uploadDesc,
        changelog: "Initial upload",
        placeholders: lines(placeholders),
        questions: lines(questions),
        outline: lines(outline),
        fieldConfig: fields,
      });
      setNotice(`Saved ${res.template.name} to S3 as v${res.template.current_version}.`);
      setUploadFile(null);
      setAnalyzed(false);
      await reload();
      setSelectedId(res.template.id);
      setTab("templates");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function onRestore(templateId: string, version: string) {
    if (!templateId) return;
    setBusy(true);
    try {
      await api.restoreVersion(templateId, version, `Restored from v${version}`);
      await reload();
      if (selectedId === templateId) {
        const v = await api.templateVersions(templateId);
        setVersions(v.versions);
      }
      setNotice(`v${version} restored as a new active version.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Restore failed");
    } finally {
      setBusy(false);
    }
  }

  const allHistory = useMemo(() => {
    const rows: Array<{
      name: string;
      version: string;
      created: string;
      modified: string;
      status: string;
      active: boolean;
      id: string;
      templateId: string;
    }> = [];
    for (const t of templates) {
      for (const v of t.versions || []) {
        rows.push({
          name: t.name,
          version: v.version,
          created: v.created_at,
          modified: v.modified_at || v.created_at,
          status: v.status,
          active: v.version === t.current_version,
          id: `${t.id}-${v.version}`,
          templateId: t.id,
        });
      }
    }
    return rows.sort((a, b) => (a.modified < b.modified ? 1 : -1));
  }, [templates]);

  const historyRows = useMemo(() => {
    const rows = selectedId
      ? versions.map((v) => ({
          name: selected?.name || v.template_name || "",
          version: v.version,
          created: v.created_at,
          modified: v.modified_at || v.created_at,
          status: v.status,
          active: Boolean(v.is_active || v.is_latest),
          id: v.version,
          templateId: selectedId,
        }))
      : allHistory;
    const q = historySearch.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((row) =>
      [row.name, `v${row.version}`, row.version, row.status, row.active ? "active" : "", fmt(row.created), fmt(row.modified)]
        .join(" ")
        .toLowerCase()
        .includes(q)
    );
  }, [allHistory, historySearch, selected?.name, selectedId, versions]);

  return (
    <div className="dash admin-shell">
      {error ? <Popup kind="error" message={error} onClose={() => setError(null)} /> : null}
      {notice ? <Popup kind="info" title="Message" message={notice} onClose={() => setNotice(null)} /> : null}
      {busy ? <ProcessingPanel label="Processing…" /> : null}

      <div className="tabs">
        {(["templates", "upload", "history", "documents", "analytics"] as Tab[]).map((id) => (
          <button key={id} type="button" className={tab === id ? "tab active" : "tab"} onClick={() => setTab(id)}>
            {id === "templates"
              ? "Template"
              : id === "upload"
                ? "Upload"
                : id === "history"
                  ? "Version History"
                  : id === "documents"
                    ? "Document History"
                    : "Analytics"}
          </button>
        ))}
      </div>

      {tab === "templates" ? (
        <div className="dash-grid">
          <section className="card list-card">
            <header className="tmpl-list-head">
              <div>
                <h3>Templates</h3>
                <p className="muted">{templates.length} stored in S3</p>
              </div>
            </header>
            <label className="tmpl-search">
              <span className="sr-only">Search templates</span>
              <input
                value={listQuery}
                onChange={(e) => setListQuery(e.target.value)}
                placeholder="Search by name or type…"
              />
            </label>
            <ul className="tmpl-list">
              {visibleTemplates.length === 0 ? (
                <li className="tmpl-empty">No templates match that search.</li>
              ) : null}
              {visibleTemplates.map((t) => {
                const kind = templateKind(t);
                const active = selectedId === t.id;
                return (
                  <li key={t.id}>
                    <button type="button" className={active ? "tmpl-card active" : "tmpl-card"} onClick={() => setSelectedId(t.id)}>
                      <span className={`file-badge file-badge-${kind}`} aria-hidden>
                        {kind}
                      </span>
                      <span className="tmpl-card-body">
                        <strong>{t.name}</strong>
                        <span className="tmpl-card-meta">
                          <span className="tmpl-pill tmpl-pill-ok">Active</span>
                          <span className="tmpl-pill">v{t.current_version || "—"}</span>
                          <span className="tmpl-pill">{t.current_status || "published"}</span>
                          {t.s3_key ? <span className="tmpl-pill tmpl-pill-s3">S3</span> : null}
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>

          <section className={`card config-card${selected && !panelReady ? " is-loading" : ""}`}>
            {!selected ? (
              <div className="empty-state">
                <div className="empty-art" aria-hidden>
                  <span className="empty-sheet empty-sheet-3" />
                  <span className="empty-sheet empty-sheet-2" />
                  <span className="empty-sheet empty-sheet-1">
                    <i />
                    <i />
                    <i />
                  </span>
                </div>
                <h3>Choose a template to begin</h3>
                <p>
                  Pick a file from the list on the left to preview, edit fields, and save a new version.
                </p>
                <button type="button" className="primary" onClick={() => setTab("upload")}>
                  Upload a new template
                </button>
              </div>
            ) : !panelReady ? (
              <div className="config-loading">
                <ProcessingPanel label="Loading template details…" />
                <p className="muted">The editor stays locked until placeholders and version data are ready.</p>
              </div>
            ) : (
              <form className="config-form" onSubmit={(e) => void saveVersion(e)}>
                <fieldset className="config-lock" disabled={busy} aria-busy={busy}>
                  <header className="config-head">
                    <div className="config-title">
                      <span className={`file-badge file-badge-${templateKind(selected)}`} aria-hidden>
                        {templateKind(selected)}
                      </span>
                      <div>
                        <h3>{selected.name}</h3>
                        <div className="tmpl-card-meta">
                          <span className="tmpl-pill tmpl-pill-ok">Active</span>
                          <span className="tmpl-pill">v{selected.current_version || "—"}</span>
                          <span className="tmpl-pill">{selected.current_status || "published"}</span>
                          {selected.s3_key ? <span className="tmpl-pill tmpl-pill-s3">S3</span> : null}
                        </div>
                      </div>
                    </div>
                    <label className="config-upload">
                      <span>Replace file (optional)</span>
                      <span className="config-upload-box">
                        <input type="file" accept=".docx,.xlsx,.pptx" onChange={(e) => setEditFile(e.target.files?.[0] || null)} />
                        <strong>{editFile ? editFile.name : "Choose a Word, Excel, or PowerPoint file"}</strong>
                      </span>
                    </label>
                  </header>
                  <div className="config-scroll">
                    <div className="config-section-title">
                      <h3>Fields</h3>
                      <span className="tmpl-pill">{fields.length} placeholder{fields.length === 1 ? "" : "s"}</span>
                    </div>
                    {fields.length === 0 ? (
                      <p className="muted">No placeholders were found in this template file yet.</p>
                    ) : (
                      <div className="field-table">
                        <div className="field-table-head">
                          <span>Field</span>
                          <span>Question</span>
                          <span>Required</span>
                          <span />
                        </div>
                        {fields.map((f, i) => (
                          <div className="field-row" key={`${f.id}-${i}`}>
                            <input
                              value={f.label}
                              aria-label="Field name"
                              onChange={(e) => setFields((prev) => prev.map((x, idx) => (idx === i ? { ...x, label: e.target.value } : x)))}
                            />
                            <input
                              value={f.question}
                              aria-label="Question"
                              onChange={(e) => setFields((prev) => prev.map((x, idx) => (idx === i ? { ...x, question: e.target.value } : x)))}
                            />
                            <label className="chk">
                              <input
                                type="checkbox"
                                checked={f.required}
                                onChange={(e) => setFields((prev) => prev.map((x, idx) => (idx === i ? { ...x, required: e.target.checked } : x)))}
                              />
                              Required
                            </label>
                            <button type="button" className="ghost field-remove" onClick={() => setFields((prev) => prev.filter((_, idx) => idx !== i))}>
                              Remove
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                    <button
                      type="button"
                      className="ghost add-field-btn"
                      onClick={() =>
                        setFields((prev) => [
                          ...prev,
                          { id: `field_${prev.length + 1}`, label: "New field", question: "What is this field?", required: false, field_type: "string", source: "manual" },
                        ])
                      }
                    >
                      Add question
                    </button>
                    <label className="field">
                      <span>Sections</span>
                      <textarea value={outline} onChange={(e) => setOutline(e.target.value)} rows={3} />
                    </label>
                    <label className="field">
                      <span>Change description</span>
                      <input value={changelog} onChange={(e) => setChangelog(e.target.value)} placeholder="What changed?" required />
                    </label>
                  </div>
                  <div className="btn-row config-actions">
                    <button type="button" className="ghost" onClick={() => void previewExisting()}>
                      Preview
                    </button>
                    <button type="submit" className="primary">
                      {busy ? "Saving…" : "Save new version"}
                    </button>
                    <button
                      type="button"
                      className="ghost"
                      onClick={() => {
                        setChangelog("");
                        setEditFile(null);
                        setNotice("Edit cancelled.");
                      }}
                    >
                      Cancel
                    </button>
                  </div>
                </fieldset>
              </form>
            )}
          </section>
        </div>
      ) : null}

      {tab === "upload" ? (
        <section className="card">
          <h3>Upload template</h3>
          <div className="dropzone">
            <input type="file" accept=".docx,.xlsx,.pptx" onChange={(e) => { setUploadFile(e.target.files?.[0] || null); setAnalyzed(false); }} />
            <span>{uploadFile ? uploadFile.name : "Choose a Word, Excel, or PowerPoint template"}</span>
          </div>
          <div className="btn-row">
            <button type="button" className="primary" disabled={!uploadFile || busy} onClick={() => void onAnalyzeUpload()}>
              Analyze placeholders
            </button>
          </div>
          {analyzed ? (
            <div className="stack" style={{ marginTop: "1rem" }}>
              <label className="field">
                <span>Template name</span>
                <input value={uploadName} onChange={(e) => setUploadName(e.target.value)} />
              </label>
              <label className="field">
                <span>Description</span>
                <textarea value={uploadDesc} onChange={(e) => setUploadDesc(e.target.value)} rows={3} />
              </label>
              {fields.map((f, i) => (
                <div className="field-row" key={`${f.id}-${i}`}>
                  <input value={f.label} onChange={(e) => setFields((prev) => prev.map((x, idx) => (idx === i ? { ...x, label: e.target.value } : x)))} />
                  <input value={f.question} onChange={(e) => setFields((prev) => prev.map((x, idx) => (idx === i ? { ...x, question: e.target.value } : x)))} />
                  <label className="chk">
                    <input type="checkbox" checked={f.required} onChange={(e) => setFields((prev) => prev.map((x, idx) => (idx === i ? { ...x, required: e.target.checked } : x)))} />
                    Required
                  </label>
                </div>
              ))}
              <div className="btn-row">
                <button type="button" className="ghost" disabled={busy} onClick={() => void onPreviewUpload()}>
                  Preview
                </button>
                <button type="button" className="primary" disabled={busy} onClick={() => void onSaveUpload()}>
                  Save
                </button>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    setUploadFile(null);
                    setAnalyzed(false);
                    setNotice("Upload cancelled — S3 was not changed.");
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : null}
        </section>
      ) : null}

      {tab === "history" ? (
        <section className="card">
          <div className="panel-head">
            <div>
              <h3>Version history</h3>
          </div>
            <label className="search-field">
              <span className="sr-only">Search version history</span>
              <input
                type="search"
                value={historySearch}
                onChange={(e) => setHistorySearch(e.target.value)}
                placeholder="Search versions…"
              />
            </label>
          </div>
          <table className="ver-table">
            <thead>
              <tr>
                <th>Document / Template</th>
                <th>Version</th>
                <th>Created</th>
                <th>Modified</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {historyRows.length ? (
                historyRows.map((row) => (
                <tr key={row.id}>
                  <td>{row.name}</td>
                  <td>
                    <strong>v{row.version}</strong>
                    {row.active ? <span className="badge">active</span> : null}
                  </td>
                  <td>{fmt(row.created)}</td>
                  <td>{fmt(row.modified)}</td>
                  <td>
                    <span className={`status status-${row.status}`}>{row.status}</span>
                  </td>
                  <td>
                    {row.active ? (
                      <span className="muted">Current</span>
                    ) : (
                      <button
                        type="button"
                        className="ghost"
                        disabled={busy}
                        onClick={() => void onRestore(row.templateId, row.version)}
                      >
                        Restore
                      </button>
                    )}
                  </td>
                </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={6} className="muted">
                    {historySearch.trim() ? "No versions match your search." : "No version history yet."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          {selectedId && versions.length > 1 ? (
            <>
              <div className="compare-row">
                <label className="field">
                  <span>From</span>
                  <select value={fromVer} onChange={(e) => setFromVer(e.target.value)}>
                    {versions.map((v) => (
                      <option key={`f-${v.version}`} value={v.version}>v{v.version}</option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>To</span>
                  <select value={toVer} onChange={(e) => setToVer(e.target.value)}>
                    {versions.map((v) => (
                      <option key={`t-${v.version}`} value={v.version}>v{v.version}</option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    if (fromVer === toVer) {
                      setError("Choose two different versions to compare.");
                      return;
                    }
                    setBusy(true);
                    setCompare(null);
                    void api
                      .compareVersions(selectedId, fromVer, toVer)
                      .then(setCompare)
                      .catch((e) => setError(e instanceof Error ? e.message : "Compare failed"))
                      .finally(() => setBusy(false));
                  }}
                  disabled={busy || fromVer === toVer}
                >
                  Compare
                </button>
              </div>
              {compare ? (
                <div className="compare-result">
                  <p className="muted">{compare.summary}</p>
                  {compare.changes?.length ? (
                    <ul className="change-list">
                      {compare.changes.slice(0, 12).map((c, i) => (
                        <li key={`${c.field}-${i}`}>
                          <strong>{c.field}</strong>{" "}
                          <span className={`change-tag change-${c.change}`}>{c.change}</span>
                          {c.change === "updated" ? (
                            <span>
                              {" "}
                              {String(c.before || "").slice(0, 80)} → {String(c.after || "").slice(0, 80)}
                            </span>
                          ) : (
                            <span> {String(c.after || c.before || "").slice(0, 120)}</span>
                          )}
                        </li>
                      ))}
                      {compare.changes.length > 12 ? (
                        <li className="muted">… {compare.changes.length - 12} more in the diff below</li>
                      ) : null}
                    </ul>
                  ) : null}
                  <DiffView lines={compare.unified_diff} />
                </div>
              ) : null}
            </>
          ) : null}
        </section>
      ) : null}

      {tab === "documents" ? (
        <section className="card">
          <h3>Document history</h3>
          <table className="ver-table">
            <thead>
              <tr>
                <th>Document</th>
                <th>Template</th>
                <th>Version</th>
                <th>Created</th>
                <th>Modified</th>
                <th>S3</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={String(d.id)}>
                  <td>{String(d.document_name || d.filename)}</td>
                  <td>{String(d.template_name)}</td>
                  <td>v{String(d.template_version)}</td>
                  <td>{fmt(String(d.created_at || ""))}</td>
                  <td>{fmt(String(d.modified_at || d.created_at || ""))}</td>
                  <td className="muted">{String(d.s3_uri || d.s3_key || "—")}</td>
                  <td>{String(d.status || "generated")}</td>
                  <td>
                    <div className="btn-row">
                      <button
                        type="button"
                        className="ghost"
                        onClick={() =>
                          setPreview({
                            filename: String(d.filename),
                            s3Key: String(d.s3_key || ""),
                            title: String(d.document_name || d.filename),
                          })
                        }
                      >
                        Preview
                      </button>
                      <button
                        type="button"
                        className="ghost"
                        onClick={() => void api.downloadFile(String(d.filename), String(d.s3_key || "") || undefined).catch((e) => setError(e instanceof Error ? e.message : "Download failed"))}
                      >
                        Download
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {tab === "analytics" ? <S3AnalyticsPanel /> : null}

      {preview ? (
        <DocumentPreview filename={preview.filename} s3Key={preview.s3Key} title={preview.title} onClose={() => setPreview(null)} />
      ) : null}
    </div>
  );
}
