import { getAccessToken } from "./session";

export type AuthUser = {
  id: string;
  email: string;
  name: string;
  role: string;
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
  versions: Array<{
    version: string;
    status: string;
    changelog: string;
    created_at: string;
    created_by: string;
  }>;
  usage_count: number;
  last_used_at?: string | null;
};

export type TemplateSource = {
  id: string;
  name: string;
  source: "local" | "onedrive";
  output_format?: string | null;
  description: string;
  onedrive_item_id?: string | null;
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
};

export type AuthConfig = {
  mode: string;
  client_id: string;
  tenant_id: string;
  redirect_uri: string;
  scopes: string[];
  authority: string;
};

const API_BASE = import.meta.env.VITE_API_BASE || "";

type GraphOpts = { token?: string | null; user?: string };

function authHeaders(extra?: HeadersInit): Record<string, string> {
  const headers: Record<string, string> = { ...(extra as Record<string, string>) };
  const jwt = getAccessToken();
  if (jwt) headers["Authorization"] = `Bearer ${jwt}`;
  return headers;
}

function graphHeaders(opts?: GraphOpts): HeadersInit {
  const headers = authHeaders();
  if (opts?.token) headers["X-Graph-Token"] = opts.token;
  if (opts?.user) headers["X-User"] = opts.user;
  return headers;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: authHeaders({
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export const api = {
  fileUrl: (downloadUrl: string) => `${API_BASE}${downloadUrl}`,

  login: (email: string, password: string) =>
    request<{ access_token: string; user: AuthUser }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  register: (email: string, password: string, name: string, role = "consultant") =>
    request<{ access_token: string; user: AuthUser }>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, name, role }),
    }),

  me: () => request<{ user: AuthUser }>("/api/auth/me"),

  onedriveAuthConfig: () => request<AuthConfig>("/api/onedrive/auth-config"),

  templateSources: (opts?: GraphOpts) =>
    request<{ templates: TemplateSource[]; user?: AuthUser }>("/api/template-sources", {
      headers: graphHeaders(opts),
    }),

  compose: async (params: {
    prompt?: string;
    text?: string;
    file?: File | null;
    templateId?: string;
    templateSource?: string;
    onedriveItemId?: string;
    token?: string | null;
  }): Promise<UploadAnalyzeResponse> => {
    const body = new FormData();
    body.append("prompt", params.prompt || "");
    body.append("text", params.text || "");
    body.append("template_source", params.templateSource || "local");
    body.append("auto_generate", "true");
    if (params.templateId) body.append("template_id", params.templateId);
    if (params.onedriveItemId) body.append("onedrive_item_id", params.onedriveItemId);
    if (params.file) body.append("file", params.file);

    const headers = authHeaders();
    if (params.token) headers["X-Graph-Token"] = params.token;

    const res = await fetch(`${API_BASE}/api/compose`, { method: "POST", body, headers });
    if (!res.ok) {
      throw new Error((await res.text()) || res.statusText);
    }
    return res.json();
  },

  parseFile: async (file: File): Promise<{ filename: string; char_count: number; text: string }> => {
    const body = new FormData();
    body.append("file", file);
    const res = await fetch(`${API_BASE}/api/parse`, {
      method: "POST",
      body,
      headers: authHeaders(),
    });
    if (!res.ok) {
      throw new Error((await res.text()) || res.statusText);
    }
    return res.json();
  },

  templateVersions: (templateId: string) =>
    request<{
      template_id: string;
      template_name: string;
      latest_version: string;
      versions: Array<{
        version: string;
        status: string;
        changelog: string;
        created_at: string;
        created_by: string;
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
    request<{
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
      }>;
    }>(
      `/api/templates/${encodeURIComponent(templateId)}/versions/compare?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`
    ),

  createVersion: (
    templateId: string,
    body: {
      version: string;
      changelog: string;
      status?: string;
      created_by?: string;
      promote_to_current?: boolean;
    }
  ) =>
    request<{ template: TemplateMeta }>(`/api/templates/${encodeURIComponent(templateId)}/versions`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
