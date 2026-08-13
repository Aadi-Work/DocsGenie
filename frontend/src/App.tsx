import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, AuthUser, TemplateSource, UploadAnalyzeResponse } from "./api";
import AuthScreen from "./AuthScreen";
import { getGraphToken, initMsal, signIn } from "./auth";
import { clearSession, getAccessToken, getStoredUser } from "./session";

type VersionRow = {
  version: string;
  status: string;
  changelog: string;
  created_at: string;
  created_by: string;
  is_latest?: boolean;
};

type CompareResult = {
  summary: string;
  from_version: string;
  to_version: string;
  changes: Array<{ field: string; change: string; before: unknown; after: unknown }>;
};

function fmtVal(v: unknown): string {
  if (v == null) return "—";
  if (Array.isArray(v)) return v.join(", ") || "—";
  return String(v);
}

export default function App() {
  const [user, setUser] = useState<AuthUser | null>(() => getStoredUser());
  const [authChecking, setAuthChecking] = useState(!!getAccessToken());
  const [templates, setTemplates] = useState<TemplateSource[]>([]);
  const [templateKey, setTemplateKey] = useState("");
  const [prompt, setPrompt] = useState("");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [parsedText, setParsedText] = useState("");
  const [parsing, setParsing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<UploadAnalyzeResponse | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const [versions, setVersions] = useState<VersionRow[]>([]);
  const [versionTemplateName, setVersionTemplateName] = useState("");
  const [fromVer, setFromVer] = useState("");
  const [toVer, setToVer] = useState("");
  const [compare, setCompare] = useState<CompareResult | null>(null);
  const [newVersion, setNewVersion] = useState("");
  const [newChangelog, setNewChangelog] = useState("");
  const [versionBusy, setVersionBusy] = useState(false);

  const selected = useMemo(
    () => templates.find((t) => t.id === templateKey) || null,
    [templates, templateKey]
  );

  const localTemplateId = selected?.source === "local" ? selected.id : "";

  useEffect(() => {
    void (async () => {
      const token = getAccessToken();
      if (!token) {
        setAuthChecking(false);
        setUser(null);
        return;
      }
      try {
        const me = await api.me();
        setUser(me.user);
      } catch {
        clearSession();
        setUser(null);
      } finally {
        setAuthChecking(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (!user) return;
    void (async () => {
      try {
        await initMsal();
        try {
          await signIn();
        } catch {
          /* ignore */
        }
        const token = await getGraphToken();
        const res = await api.templateSources({ token, user: user.email });
        setTemplates(res.templates);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not load templates. Start the backend.");
      }
    })();
  }, [user]);

  useEffect(() => {
    setCompare(null);
    setVersions([]);
    setFromVer("");
    setToVer("");
    if (!localTemplateId) return;
    void (async () => {
      try {
        const res = await api.templateVersions(localTemplateId);
        setVersions(res.versions);
        setVersionTemplateName(res.template_name);
        if (res.versions.length >= 2) {
          setToVer(res.versions[0].version);
          setFromVer(res.versions[1].version);
        } else if (res.versions.length === 1) {
          setFromVer(res.versions[0].version);
          setToVer(res.versions[0].version);
        }
      } catch {
        setVersions([]);
      }
    })();
  }, [localTemplateId]);

  async function onFileChosen(next: File | null) {
    setFile(next);
    setParsedText("");
    setResult(null);
    if (!next) return;
    setParsing(true);
    setError(null);
    try {
      const parsed = await api.parseFile(next);
      setParsedText(parsed.text);
    } catch (err) {
      setFile(null);
      setParsedText("");
      if (fileRef.current) fileRef.current.value = "";
      setError(err instanceof Error ? err.message : "Could not parse file");
    } finally {
      setParsing(false);
    }
  }

  async function onGenerate(e?: FormEvent) {
    e?.preventDefault();
    if (busy || parsing) return;
    if (!text.trim() && !file && !prompt.trim()) {
      setError("Add some text, attach a file, or write a prompt.");
      return;
    }
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const token = await getGraphToken();
      const res = await api.compose({
        prompt,
        text,
        file,
        templateId: selected?.source === "local" ? selected.id : undefined,
        templateSource: selected?.source || "local",
        onedriveItemId: selected?.onedrive_item_id || undefined,
        token,
      });
      setResult(res);
      if (res.preview) setParsedText(res.preview);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Generation failed");
    } finally {
      setBusy(false);
    }
  }

  async function onCompare() {
    if (!localTemplateId || !fromVer || !toVer) return;
    setVersionBusy(true);
    setError(null);
    try {
      const res = await api.compareVersions(localTemplateId, fromVer, toVer);
      setCompare(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Compare failed");
    } finally {
      setVersionBusy(false);
    }
  }

  async function onCreateVersion(e: FormEvent) {
    e.preventDefault();
    if (!localTemplateId || !newVersion.trim() || !newChangelog.trim()) return;
    setVersionBusy(true);
    setError(null);
    try {
      await api.createVersion(localTemplateId, {
        version: newVersion.trim(),
        changelog: newChangelog.trim(),
        status: "approved",
        created_by: user?.email || "consultant",
        promote_to_current: true,
      });
      const res = await api.templateVersions(localTemplateId);
      setVersions(res.versions);
      setNewVersion("");
      setNewChangelog("");
      if (res.versions.length >= 2) {
        setToVer(res.versions[0].version);
        setFromVer(res.versions[1].version);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create version");
    } finally {
      setVersionBusy(false);
    }
  }

  function logout() {
    clearSession();
    setUser(null);
    setTemplates([]);
    setResult(null);
  }

  if (authChecking) {
    return (
      <div className="page">
        <div className="glow" aria-hidden />
        <p className="muted">Checking session…</p>
      </div>
    );
  }

  if (!user) {
    return <AuthScreen onAuthenticated={setUser} />;
  }

  return (
    <div className="page">
      <div className="glow" aria-hidden />

      <header className="hero">
        <div className="hero-top">
          <div>
            <p className="logo">YMSLI</p>
            <h1>Template Hub</h1>
          </div>
          <div className="user-chip">
            <span>
              {user.name}
              <small>
                {user.email} · {user.role}
              </small>
            </span>
            <button type="button" className="ghost" onClick={logout}>
              Sign out
            </button>
          </div>
        </div>
        <p className="tagline">Pick a template, add your content, get a finished document.</p>
      </header>

      <form className="card" onSubmit={(e) => void onGenerate(e)}>
        <label className="field">
          <span>Template</span>
          <select value={templateKey} onChange={(e) => setTemplateKey(e.target.value)} disabled={busy}>
            <option value="">Auto — detect from prompt / content</option>
            <optgroup label="Local">
              {templates
                .filter((t) => t.source === "local")
                .map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                    {t.output_format ? ` (.${t.output_format})` : ""}
                  </option>
                ))}
            </optgroup>
            <optgroup label="OneDrive">
              {templates
                .filter((t) => t.source === "onedrive")
                .map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
            </optgroup>
          </select>
          {selected ? (
            <small>{selected.description}</small>
          ) : (
            <small>Or name the template in your prompt, e.g. “Create a MOM…”</small>
          )}
        </label>

        <label className="field">
          <span>Prompt</span>
          <input
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder='e.g. "Create a QMM proposal from the notes below"'
            disabled={busy}
          />
        </label>

        <label className="field">
          <span>Your content</span>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste meeting notes, requirements, or any source text…"
            rows={8}
            disabled={busy}
          />
        </label>

        <div className="attach">
          <input
            ref={fileRef}
            type="file"
            accept=".txt,.md,.docx,.pdf,.xlsx,.csv"
            hidden
            onChange={(e) => void onFileChosen(e.target.files?.[0] || null)}
          />
          <button type="button" className="ghost" disabled={busy || parsing} onClick={() => fileRef.current?.click()}>
            {parsing ? "Parsing…" : file ? file.name : "Attach file (optional)"}
          </button>
          {file ? (
            <button
              type="button"
              className="ghost danger"
              disabled={busy || parsing}
              onClick={() => {
                void onFileChosen(null);
                if (fileRef.current) fileRef.current.value = "";
              }}
            >
              Remove
            </button>
          ) : null}
        </div>

        {file || parsedText ? (
          <div className="field parsed">
            <span>Parsed file content{file ? ` · ${file.name}` : ""}</span>
            {parsing ? (
              <p className="muted">Extracting text…</p>
            ) : (
              <pre className="parsed-box">{parsedText || "No text extracted."}</pre>
            )}
          </div>
        ) : null}

        <button type="submit" className="primary" disabled={busy || parsing}>
          {busy ? "Generating…" : "Generate document"}
        </button>

        {error ? <p className="error">{error}</p> : null}
      </form>

      {localTemplateId && versions.length > 0 ? (
        <section className="card versions-card">
          <h2>Versions · {versionTemplateName}</h2>
          <ul className="version-list">
            {versions.map((v) => (
              <li key={v.version}>
                <strong>
                  v{v.version}
                  {v.is_latest ? " · latest" : ""}
                </strong>
                <span className="muted">
                  [{v.status}] {v.changelog} — {v.created_by} · {v.created_at.slice(0, 10)}
                </span>
              </li>
            ))}
          </ul>

          <div className="compare-row">
            <label className="field">
              <span>From</span>
              <select value={fromVer} onChange={(e) => setFromVer(e.target.value)} disabled={versionBusy}>
                {versions.map((v) => (
                  <option key={`from-${v.version}`} value={v.version}>
                    v{v.version}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>To</span>
              <select value={toVer} onChange={(e) => setToVer(e.target.value)} disabled={versionBusy}>
                {versions.map((v) => (
                  <option key={`to-${v.version}`} value={v.version}>
                    v{v.version}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="ghost" disabled={versionBusy || !fromVer || !toVer} onClick={() => void onCompare()}>
              Compare
            </button>
          </div>

          {compare ? (
            <div className="compare-result">
              <p>
                <strong>
                  v{compare.from_version} → v{compare.to_version}
                </strong>
              </p>
              <p className="muted">{compare.summary}</p>
              <ul className="fields">
                {compare.changes
                  .filter((c) => c.change !== "unchanged" && !c.field.includes(":"))
                  .map((c) => (
                    <li key={c.field}>
                      <strong>
                        {c.field} · {c.change}
                      </strong>
                      <span className="diff-before">Before: {fmtVal(c.before)}</span>
                      <span className="diff-after">After: {fmtVal(c.after)}</span>
                    </li>
                  ))}
              </ul>
            </div>
          ) : null}

          <form className="new-version" onSubmit={(e) => void onCreateVersion(e)}>
            <p className="field-label">Save new version</p>
            <div className="compare-row">
              <input
                value={newVersion}
                onChange={(e) => setNewVersion(e.target.value)}
                placeholder="e.g. 1.3"
                disabled={versionBusy}
              />
              <input
                value={newChangelog}
                onChange={(e) => setNewChangelog(e.target.value)}
                placeholder="What changed?"
                disabled={versionBusy}
              />
              <button type="submit" className="ghost" disabled={versionBusy || !newVersion.trim() || !newChangelog.trim()}>
                Save
              </button>
            </div>
          </form>
        </section>
      ) : null}

      {result ? (
        <section className="card result">
          <h2>Ready</h2>
          <p>
            Used <strong>{result.template.name}</strong>
            {result.template_source === "onedrive" ? " (from OneDrive match)" : ""} ·{" "}
            {(result.confidence * 100).toFixed(0)}% confidence
          </p>
          <p className="muted">{result.selection_reason}</p>
          <ul className="fields">
            {Object.entries(result.filled_fields)
              .slice(0, 8)
              .map(([k, v]) => (
                <li key={k}>
                  <strong>{k}</strong>
                  <span>{v}</span>
                </li>
              ))}
          </ul>
          {result.download_url ? (
            <a className="primary link" href={api.fileUrl(result.download_url)} target="_blank" rel="noreferrer">
              Download {result.filename || "document"}
            </a>
          ) : (
            <p className="error">Document was not generated (check write access).</p>
          )}
        </section>
      ) : null}
    </div>
  );
}
