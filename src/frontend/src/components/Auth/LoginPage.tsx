import { useState } from "react";
import { LockKeyhole } from "lucide-react";
import { api, setAuthToken } from "../../services/api.service";

export interface AuthUser {
  userId: string;
  username: string;
  role: string;
  team: string;
  allowedServices?: string[];
  allowedSourceApps?: string[];
}

export function LoginPage({ onLogin }: { onLogin: (user: AuthUser) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    if (!username.trim() || !password || loading) return;
    setLoading(true);
    setError("");
    try {
      const response = await api.login(username.trim(), password);
      const token = String(response.accessToken || "");
      const user = response.user as unknown as AuthUser;
      if (!token || !user?.userId) throw new Error("Invalid login response");
      setAuthToken(token);
      onLogin(user);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-icon"><LockKeyhole size={24} /></div>
        <h1>Agentic BI</h1>
        <p>Sign in to access governed analytics.</p>
        <label>
          <span>Username</span>
          <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" />
        </label>
        <label>
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            onKeyDown={(event) => event.key === "Enter" && submit()}
          />
        </label>
        {error && <div className="login-error">{error}</div>}
        <button className="btn btn-primary" type="button" onClick={submit} disabled={loading}>
          {loading ? "Signing in..." : "Sign in"}
        </button>
      </div>
    </div>
  );
}
