import { useEffect, useState } from "react";
import { api, AuthUser } from "./api";
import AdminDashboard from "./AdminDashboard";
import AuthScreen from "./AuthScreen";
import EmployeeDashboard from "./EmployeeDashboard";
import { ProcessingPanel } from "./RequestProgress";
import { clearSession, getAccessToken, getStoredUser } from "./session";

export default function App() {
  const [user, setUser] = useState<AuthUser | null>(() => getStoredUser());
  const [authChecking, setAuthChecking] = useState(!!getAccessToken());

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

  function logout() {
    clearSession();
    setUser(null);
  }

  if (authChecking) {
    return (
      <div className="page">
        <div className="glow" aria-hidden />
        <ProcessingPanel label="Checking session…" />
      </div>
    );
  }

  if (!user) {
    return <AuthScreen onAuthenticated={setUser} />;
  }

  const isAdmin = user.role === "admin" || user.is_admin;

  return (
    <div className="page wide">
      <div className="glow" aria-hidden />
      <header className="hero app-bar">
        <div className="hero-top">
          <div className="brand">
            <p className="logo">YMSLI</p>
            <h1>Template Hub</h1>
          </div>
          <div className="user-chip">
            <span>
              {user.name}
              <small>
                {user.email} · {isAdmin ? "Admin" : "Employee"}
              </small>
            </span>
            <button type="button" className="ghost" onClick={logout}>
              Sign out
            </button>
          </div>
        </div>
      </header>

      {isAdmin ? <AdminDashboard userName={user.name} /> : <EmployeeDashboard userName={user.name} />}
    </div>
  );
}
