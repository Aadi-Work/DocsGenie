import { getAccessToken } from "./session";

export type AuthUser = {
  id: string;
  email: string;
  name: string;
  role: "admin" | "employee" | string;
  is_admin?: boolean;
};

export type PlaceholderField = {
  id: string;
  label: string;
  question: string;
  required: boolean;
  field_type: string;
  source: string;
  help?: string;
};

export type TemplateMeta = {
  id: string;
  name: string;
  category: string;
  description: string;
  tags: string[];
  output_format: string;
  placeholders: string[];
  context_questions: string[];
  content_outline: string[];
  current_version?: string | null;
  current_status?: string | null;
  s3_key?: string | null;
  s3_uri?: string | null;
  created_at?: string | null;
  modified_at?: string | null;
  created_by?: string | null;
  field_config?: PlaceholderField[];
  original_filename?: string | null;
  versions: Array<{
    version: string;
    status: string;
    changelog: string;
    created_at: string;
    created_by: string;
    modified_at?: string | null;
    previous_version?: string | null;
    s3_key?: string | null;
    is_latest?: boolean;
    is_active?: boolean;
    template_name?: string;
  }>;
  usage_count: number;
};

export type TemplateSource = {
  id: string;
  name: string;
  source: "s3" | "local";
  output_format?: string | null;
  description: string;
  s3_key?: string | null;
  current_version?: string | null;
  status?: string | null;
  profile_id?: string | null;
  guided?: boolean;
  entry_mode?: "form" | "chat";
  original_filename?: string | null;
  sample_file?: string | null;
  format_help?: string | null;
  sample_notes?: string | null;
  field_config?: PlaceholderField[];
};

export type DiffLine = {
  type: "added" | "removed" | "unchanged" | "context";
  text: string;
  old_line_no?: number | null;
  new_line_no?: number | null;
};

export type VersionCompare = {
  template_id: string;
  template_name: string;
  from_version: string;
  to_version: string;
  summary: string;
  changes: Array<{
    field: string;
    change: string;
    before: unknown;
    after: unknown;
    lines: DiffLine[];
  }>;
  unified_diff: DiffLine[];
};

export type S3Analytics = {
  bucket: string;
  generated_at: string;
  totals: {
    objects: number;
    bytes: number;
    office_files: number;
    templates: number;
    generated_documents: number;
    versions: number;
  };
  by_area: Array<{ id: string; label: string; count: number; bytes: number }>;
  by_kind: Array<{ id: string; label: string; count: number; bytes: number }>;
  most_used: Array<{
    id: string;
    name: string;
    format: string;
    usage_count: number;
    generated_count: number;
    score: number;
    size: number;
    storage_bytes: number;
    versions: number;
    s3_key?: string | null;
  }>;
  largest: Array<{
    name: string;
    s3_key: string;
    size: number;
    last_modified?: string | null;
    area: string;
    kind: string;
  }>;
  recent: Array<{
    name: string;
    s3_key: string;
    size: number;
    last_modified?: string | null;
    area: string;
    kind: string;
  }>;
  activity: Array<{ day: string; templates: number; generated: number; other: number; count: number }>;
};

export type UploadAnalyzeResponse = {
  detected_doc_type: string;
  summary: string;
  selection_reason: string;
  confidence: number;
  template: TemplateMeta;
  filled_fields: Record<string, string>;
  missing_fields: string[];
  preview: string;
  filename?: string | null;
  download_url?: string | null;
  auto_generated: boolean;
  llm_provider: string;
  template_source?: string;
  template_version?: string | null;
  fill_mode?: string | null;
  s3_key?: string | null;
  profile_id?: string | null;
  format_help?: string | null;
  sample_notes?: string | null;
  kb?: {
    used?: boolean;
    source?: string;
    s3_key?: string;
    process?: string;
    filled?: string[];
    error?: string;
  } | null;
};

export function filenameFromUrl(url: string): string {
  const path = (url || "").split("?")[0];
  return decodeURIComponent(path.split("/").filter(Boolean).pop() || "");
}

const API_BASE = import.meta.env.VITE_API_BASE || "";

type RequestListener = (count: number) => void;
const requestListeners = new Set<RequestListener>();
let pendingRequests = 0;

function notifyRequestActivity() {
  requestListeners.forEach((fn) => fn(pendingRequests));
}

export function subscribeRequestActivity(listener: RequestListener): () => void {
  requestListeners.add(listener);
  listener(pendingRequests);
  return () => {
    requestListeners.delete(listener);
  };
}

async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  pendingRequests += 1;
  notifyRequestActivity();
  try {
    return await fetch(input, init);
  } finally {
    pendingRequests = Math.max(0, pendingRequests - 1);
    notifyRequestActivity();
  }
}

function authHeaders(extra?: HeadersInit): Record<string, string> {
  const headers: Record<string, string> = { ...(extra as Record<string, string>) };
  const jwt = getAccessToken();
  if (jwt) headers["Authorization"] = `Bearer ${jwt}`;
  return headers;
}

function errorFromBody(text: string, fallback: string): string {
  const raw = (text || "").trim();
  if (!raw) return fallback;
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
    if (Array.isArray(parsed.detail)) {
      return parsed.detail
        .map((item) =>
          typeof item === "object" && item && "msg" in item ? String((item as { msg: string }).msg) : String(item),
        )
        .join(" ");
    }
  } catch {
    /* not JSON */
  }
  return raw;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(`${API_BASE}${path}`, {
    ...init,
    headers: authHeaders({
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    }),
  });
  if (!res.ok) throw new Error(errorFromBody(await res.text(), res.statusText));
  return res.json() as Promise<T>;
}

export const api = {
  fileUrl: (downloadUrl: string) => `${API_BASE}${downloadUrl}`,

  filenameFromUrl,

  previewPdf: async (filename: string, s3Key?: string | null) => {
    const q = s3Key ? `?s3_key=${encodeURIComponent(s3Key)}` : "";
    const res = await apiFetch(`${API_BASE}/api/preview/${encodeURIComponent(filename || "document")}${q}`, {
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(errorFromBody(await res.text(), res.statusText));
    return res.blob();
  },

  previewTemplate: async (templateId: string) => {
    const res = await apiFetch(`${API_BASE}/api/templates/${encodeURIComponent(templateId)}/preview`, {
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(errorFromBody(await res.text(), res.statusText));
    return res.blob();
  },

  downloadFile: async (filename: string, s3Key?: string | null) => {
    const q = new URLSearchParams({ disposition: "attachment" });
    if (s3Key) q.set("s3_key", s3Key);
    const res = await apiFetch(`${API_BASE}/api/files/${encodeURIComponent(filename)}?${q.toString()}`, {
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(errorFromBody(await res.text(), res.statusText));
    const blob = await res.blob();
    const href = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = href;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(href);
  },

  login: (email: string, password: string) =>
    request<{ access_token: string; user: AuthUser }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  register: (email: string, password: string, name: string) =>
    request<{ access_token: string; user: AuthUser }>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, name, role: "employee" }),
    }),

  me: () => request<{ user: AuthUser }>("/api/auth/me"),

  s3Health: () => request<Record<string, unknown>>("/api/s3/health"),

  s3Search: (query: string, scope: "templates" | "documents" = "templates") =>
    request<{ items: Array<Record<string, unknown>>; count: number }>("/api/s3/search", {
      method: "POST",
      body: JSON.stringify({ query, scope, limit: 25 }),
    }),

  chat: (
    message: string,
    sessionId?: string | null,
    templateId?: string | null,
    extra?: { attachmentText?: string; attachmentName?: string },
  ) =>
    request<{
      session_id: string;
      reply: string;
      stage: string;
      download_url?: string | null;
      preview_url?: string | null;
      template?: TemplateMeta | null;
      search_results?: TemplateMeta[];
      answers?: Record<string, string>;
      questions?: string[];
      template_preview_filename?: string | null;
      generated_filename?: string | null;
      s3_key?: string | null;
      current_question?: string | null;
      missing_fields?: string[];
      generation_status?: string;
      messages: Array<{ role: string; content: string }>;
    }>("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        message,
        session_id: sessionId || null,
        template_id: templateId || null,
        attachment_text: extra?.attachmentText || null,
        attachment_name: extra?.attachmentName || null,
      }),
    }),

  templateSources: () =>
    request<{ templates: TemplateSource[]; user?: AuthUser }>("/api/template-sources"),

  listTemplates: () => request<{ templates: TemplateMeta[] }>("/api/templates"),

  getTemplate: (id: string) =>
    request<{ template: TemplateMeta; latest_version: { version: string; status: string } | null }>(
      `/api/templates/${encodeURIComponent(id)}`
    ),

  createTemplate: (body: Record<string, unknown>) =>
    request<{ template: TemplateMeta }>("/api/templates", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  templateVersions: (templateId: string) =>
    request<{
      template_id: string;
      template_name: string;
      latest_version: string | null;
      versions: Array<{
        version: string;
        status: string;
        changelog: string;
        created_at: string;
        created_by: string;
        previous_version?: string | null;
        is_latest?: boolean;
        snapshot?: {
          description?: string;
          placeholders?: string[];
          content_outline?: string[];
          context_questions?: string[];
        };
      }>;
    }>(`/api/templates/${encodeURIComponent(templateId)}/versions`),

  compareVersions: (templateId: string, from: string, to: string) =>
    request<VersionCompare>(
      `/api/templates/${encodeURIComponent(templateId)}/versions/compare?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`
    ),

  createVersion: (templateId: string, body: Record<string, unknown>) =>
    request<{ template: TemplateMeta }>(`/api/templates/${encodeURIComponent(templateId)}/versions`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  restoreVersion: (templateId: string, sourceVersion: string, changelog: string) =>
    request<{ template: TemplateMeta }>(
      `/api/templates/${encodeURIComponent(templateId)}/versions/restore`,
      {
        method: "POST",
        body: JSON.stringify({ source_version: sourceVersion, changelog, publish: true }),
      }
    ),

  audit: (templateId?: string) =>
    request<{ events: Array<Record<string, unknown>> }>(
      templateId ? `/api/audit?template_id=${encodeURIComponent(templateId)}` : "/api/audit"
    ),

  s3Analytics: () => request<S3Analytics>("/api/admin/analytics"),

  generatedDocuments: () =>
    request<{ documents: Array<Record<string, unknown>> }>("/api/generated-documents"),

  compose: async (params: {
    prompt?: string;
    text?: string;
    file?: File | null;
    templateFile?: File | null;
    templateId?: string;
    templateSource?: string;
    s3Key?: string;
    autoGenerate?: boolean;
  }): Promise<UploadAnalyzeResponse> => {
    const body = new FormData();
    body.append("prompt", params.prompt || "");
    body.append("text", params.text || "");
    body.append("template_source", params.templateSource || "s3");
    body.append("auto_generate", params.autoGenerate === false ? "false" : "true");
    if (params.templateId) body.append("template_id", params.templateId);
    if (params.s3Key) body.append("s3_key", params.s3Key);
    if (params.file) body.append("file", params.file);
    if (params.templateFile) body.append("template_file", params.templateFile);
    const headers = authHeaders();
    const res = await apiFetch(`${API_BASE}/api/compose`, { method: "POST", body, headers });
    if (!res.ok) throw new Error(errorFromBody(await res.text(), res.statusText));
    return res.json();
  },

  parseFile: async (file: File) => {
    const body = new FormData();
    body.append("file", file);
    const res = await apiFetch(`${API_BASE}/api/parse`, { method: "POST", body, headers: authHeaders() });
    if (!res.ok) throw new Error(errorFromBody(await res.text(), res.statusText));
    return res.json() as Promise<{ filename: string; char_count: number; text: string }>;
  },

  generate: (templateId: string, answers: Record<string, string>) =>
    request<{
      template_id: string;
      version: string;
      filename: string;
      download_url: string;
      filled_fields: Record<string, string>;
      fill_mode?: string;
      s3_key?: string | null;
      s3_uri?: string | null;
    }>("/api/generate", {
      method: "POST",
      body: JSON.stringify({ template_id: templateId, answers }),
    }),

  adminAnalyze: async (file: File) => {
    const body = new FormData();
    body.append("file", file);
    const res = await apiFetch(`${API_BASE}/api/admin/templates/analyze`, {
      method: "POST",
      body,
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(errorFromBody(await res.text(), res.statusText));
    return res.json() as Promise<{
      filename: string;
      output_format: string;
      placeholders: string[];
      field_config: PlaceholderField[];
      context_questions: string[];
      content_outline: string[];
      tables: number;
      preview_text: string;
      message: string;
    }>;
  },

  adminPreview: async (params: {
    file?: File | null;
    templateId?: string;
    notes?: string;
    answers?: Record<string, string>;
  }) => {
    const body = new FormData();
    if (params.file) body.append("file", params.file);
    if (params.templateId) body.append("template_id", params.templateId);
    body.append("notes", params.notes || "");
    body.append("answers_json", JSON.stringify(params.answers || {}));
    const res = await apiFetch(`${API_BASE}/api/admin/templates/preview`, {
      method: "POST",
      body,
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(errorFromBody(await res.text(), res.statusText));
    return res.json() as Promise<{
      filename: string;
      download_url: string;
      fill_mode: string;
      filled_fields: Record<string, string>;
      preview: boolean;
      message: string;
      s3_key?: string | null;
    }>;
  },

  adminUpload: async (params: {
    file: File;
    name: string;
    description: string;
    changelog: string;
    placeholders: string[];
    questions: string[];
    outline: string[];
    fieldConfig: PlaceholderField[];
  }) => {
    const body = new FormData();
    body.append("file", params.file);
    body.append("name", params.name);
    body.append("description", params.description);
    body.append("changelog", params.changelog);
    body.append("placeholders_json", JSON.stringify(params.placeholders));
    body.append("questions_json", JSON.stringify(params.questions));
    body.append("outline_json", JSON.stringify(params.outline));
    body.append("field_config_json", JSON.stringify(params.fieldConfig));
    const res = await apiFetch(`${API_BASE}/api/admin/templates/upload`, {
      method: "POST",
      body,
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(errorFromBody(await res.text(), res.statusText));
    return res.json() as Promise<{ template: TemplateMeta; s3: Record<string, string>; saved: boolean }>;
  },

  adminSaveVersion: async (
    templateId: string,
    params: {
      changelog: string;
      description: string;
      placeholders: string[];
      questions: string[];
      outline: string[];
      fieldConfig: PlaceholderField[];
      file?: File | null;
    }
  ) => {
    const body = new FormData();
    body.append("changelog", params.changelog);
    body.append("description", params.description);
    body.append("placeholders_json", JSON.stringify(params.placeholders));
    body.append("questions_json", JSON.stringify(params.questions));
    body.append("outline_json", JSON.stringify(params.outline));
    body.append("field_config_json", JSON.stringify(params.fieldConfig));
    if (params.file) body.append("file", params.file);
    const res = await apiFetch(`${API_BASE}/api/admin/templates/${encodeURIComponent(templateId)}/save`, {
      method: "POST",
      body,
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(errorFromBody(await res.text(), res.statusText));
    return res.json() as Promise<{ template: TemplateMeta; saved: boolean }>;
  },
};
