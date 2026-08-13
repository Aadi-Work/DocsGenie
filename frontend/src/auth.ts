import { PublicClientApplication, AccountInfo, InteractionRequiredAuthError } from "@azure/msal-browser";
import { api, AuthConfig } from "./api";

let pca: PublicClientApplication | null = null;
let authConfig: AuthConfig | null = null;
let mockAccount: { name: string; username: string } | null = null;

export async function loadAuthConfig(): Promise<AuthConfig> {
  authConfig = await api.onedriveAuthConfig();
  return authConfig;
}

export async function initMsal(): Promise<AuthConfig> {
  const cfg = await loadAuthConfig();
  if (cfg.mode === "mock" || !cfg.client_id) {
    return cfg;
  }
  pca = new PublicClientApplication({
    auth: {
      clientId: cfg.client_id,
      authority: cfg.authority,
      redirectUri: cfg.redirect_uri,
    },
    cache: { cacheLocation: "localStorage" },
  });
  await pca.initialize();
  await pca.handleRedirectPromise();
  return cfg;
}

export function getActiveAccount(): AccountInfo | { name: string; username: string } | null {
  if (mockAccount) return mockAccount;
  if (!pca) return null;
  return pca.getActiveAccount() || pca.getAllAccounts()[0] || null;
}

export async function signIn(): Promise<void> {
  const cfg = authConfig || (await loadAuthConfig());
  if (cfg.mode === "mock" || !cfg.client_id || !pca) {
    mockAccount = { name: "Demo User", username: "demo.user@ymsli.com" };
    return;
  }
  const result = await pca.loginPopup({ scopes: cfg.scopes });
  pca.setActiveAccount(result.account);
}

export async function getGraphToken(): Promise<string | null> {
  const cfg = authConfig || (await loadAuthConfig());
  if (cfg.mode === "mock" || !cfg.client_id || !pca) {
    return mockAccount ? "mock-token" : "mock-token";
  }
  const account = pca.getActiveAccount() || pca.getAllAccounts()[0];
  if (!account) return null;
  try {
    const result = await pca.acquireTokenSilent({ account, scopes: cfg.scopes });
    return result.accessToken;
  } catch (err) {
    if (err instanceof InteractionRequiredAuthError) {
      const result = await pca.acquireTokenPopup({ scopes: cfg.scopes });
      return result.accessToken;
    }
    throw err;
  }
}
