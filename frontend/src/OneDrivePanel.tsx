import { useCallback, useEffect, useState } from "react";
import { api, DriveItem, VersionTimeline } from "./api";
import { getGraphToken, getActiveAccount, isSignedIn, signIn, signOut, initMsal } from "./auth";

type Props = {
  demoUsername: string;
  onStatus?: (msg: string | null) => void;
};

function accessBadge(item: DriveItem): string {
  if (item.access === "owner") return "owner";
  if (item.can_write) return "write";
  if (item.can_read) return "read-only";
  return "no access";
}

export default function OneDrivePanel({ demoUsername, onStatus }: Props) {
  const [ready, setReady] = useState(false);
  const [mode, setMode] = useState("mock");
  const [signedIn, setSignedIn] = useState(false);
  const [accountLabel, setAccountLabel] = useState<string>("");
  const [folder, setFolder] = useState("Templates");
  const [items, setItems] = useState<DriveItem[]>([]);
  const [selected, setSelected] = useState<DriveItem | null>(null);
  const [timeline, setTimeline] = useState<VersionTimeline | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const graphUser = demoUsername === "joiner" ? "joiner@ymsli.com" : "demo.user@ymsli.com";

  const refreshAccount = useCallback(() => {
    const acc = getActiveAccount();
    setSignedIn(!!acc);
    if (acc && "username" in acc) {
      setAccountLabel(`${acc.name || ""} (${acc.username})`.trim());
    } else {
      setAccountLabel("");
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const cfg = await initMsal();
        setMode(cfg.mode);
        refreshAccount();
        setReady(true);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Auth init failed");
        setReady(true);
      }
    })();
  }, [refreshAccount]);

  async function withToken() {
    const token = await getGraphToken();
    return { token, user: graphUser };
  }

  async function loadFolder(path = folder) {
    setBusy(true);
    setError(null);
    try {
      const opts = await withToken();
      if (mode !== "mock" && !opts.token) {
        setError("Sign in to OneDrive first.");
        return;
      }
      const res = await api.onedriveFiles(path, opts);
      setItems(res.items);
      setFolder(path);
      setMode(res.mode);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to list OneDrive");
    } finally {
      setBusy(false);
    }
  }

  async function handleSignIn() {
    setBusy(true);
    setError(null);
    try {
      await signIn();
      refreshAccount();
      await loadFolder(folder);
      onStatus?.("Signed in to OneDrive");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleSignOut() {
    await signOut();
    refreshAccount();
    setItems([]);
    setSelected(null);
    setTimeline(null);
  }

  async function openItem(item: DriveItem) {
    if (item.kind === "folder") {
      const next = item.path.replace(/^\//, "");
      await loadFolder(next);
      return;
    }
    setSelected(item);
    setBusy(true);
    setError(null);
    try {
      const opts = await withToken();
      const versions = await api.onedriveVersions(item.id, opts);
      setTimeline(versions);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load versions");
    } finally {
      setBusy(false);
    }
  }

  async function runSearch() {
    if (!query.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const opts = await withToken();
      const res = await api.onedriveSearch(query.trim(), opts);
      setItems(res.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Search failed");
    } finally {
      setBusy(false);
    }
  }

  async function restore(versionId: string) {
    if (!selected) return;
    if (!selected.can_write && timeline && !timeline.access.can_write) {
      setError("Write access required to restore a version.");
      return;
    }
    setBusy(true);
    try {
      const opts = await withToken();
      await api.onedriveRestore(
        selected.id,
        versionId,
        `revert: restore OneDrive version ${versionId}`,
        opts
      );
      const versions = await api.onedriveVersions(selected.id, opts);
      setTimeline(versions);
      onStatus?.(`Restored version ${versionId}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Restore failed");
    } finally {
      setBusy(false);
    }
  }

  if (!ready) {
    return (
      <div className="side-block">
        <h2>OneDrive</h2>
        <p className="muted">Loading Graph auth…</p>
      </div>
    );
  }

  return (
    <div className="side-block onedrive-panel">
      <div className="od-header">
        <h2>OneDrive</h2>
        <span className={`od-mode ${mode}`}>{mode}</span>
      </div>

      {!signedIn && mode === "live" ? (
        <button type="button" className="od-primary" onClick={() => void handleSignIn()} disabled={busy}>
          Sign in with Microsoft
        </button>
      ) : (
        <div className="od-account">
          <p className="muted">{accountLabel || (mode === "mock" ? `Mock · acting as ${graphUser}` : "Signed in")}</p>
          <div className="od-actions">
            {mode === "mock" && !signedIn ? (
              <button type="button" onClick={() => void handleSignIn()} disabled={busy}>
                Connect mock drive
              </button>
            ) : null}
            {signedIn || mode === "mock" ? (
              <button type="button" onClick={() => void loadFolder(folder)} disabled={busy}>
                Refresh
              </button>
            ) : null}
            {signedIn ? (
              <button type="button" onClick={() => void handleSignOut()}>
                Sign out
              </button>
            ) : null}
          </div>
        </div>
      )}

      <div className="od-search">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search OneDrive…"
          aria-label="Search OneDrive"
        />
        <button type="button" onClick={() => void runSearch()} disabled={busy || (mode === "live" && !isSignedIn())}>
          Go
        </button>
      </div>

      <p className="meta">Folder: /{folder || ""}</p>
      {folder !== "Templates" && folder !== "" ? (
        <button type="button" className="linkish" onClick={() => void loadFolder("Templates")}>
          ← Templates
        </button>
      ) : null}

      <ul className="library od-list">
        {items.map((item) => (
          <li key={item.id}>
            <button type="button" onClick={() => void openItem(item)} disabled={busy}>
              <span>
                {item.kind === "folder" ? "📁 " : "📄 "}
                {item.name}
              </span>
              <small>
                {accessBadge(item)}
                {item.kind === "file" ? ` · ${item.size} B` : ""}
              </small>
            </button>
          </li>
        ))}
      </ul>

      {selected && timeline ? (
        <div className="od-versions">
          <p className="tmpl-name">{timeline.item_name}</p>
          <p className="meta">
            Access: {timeline.access.access} · read {String(timeline.access.can_read)} · write{" "}
            {String(timeline.access.can_write)}
          </p>
          <p className="muted">{timeline.access.rationale}</p>

          <h3>Commits</h3>
          <ul className="versions">
            {timeline.hub_commits.map((c) => (
              <li key={c.sha}>
                <strong>{c.sha}</strong> {c.message}
                <br />
                <span className="muted">
                  {c.author} · {c.created_at.slice(0, 19)}
                </span>
              </li>
            ))}
            {!timeline.hub_commits.length ? <li className="muted">No hub commits yet</li> : null}
          </ul>

          <h3>OneDrive snapshots</h3>
          <ul className="versions">
            {timeline.onedrive_versions.map((v) => (
              <li key={v.id}>
                <strong>{v.id}</strong> · {v.modified_by || "unknown"} · {String(v.last_modified || "").slice(0, 19)}
                {timeline.access.can_write ? (
                  <>
                    {" "}
                    <button type="button" className="linkish" onClick={() => void restore(v.id)} disabled={busy}>
                      Restore
                    </button>
                  </>
                ) : (
                  <span className="muted"> · read-only</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {error ? <p className="error">{error}</p> : null}
    </div>
  );
}
