import { useCallback, useEffect, useRef, useState } from "react";

type AuthStatus = "authenticated" | "missing";

interface PlatformStatus {
  status: AuthStatus;
  connecting: boolean;
}

interface AuthStatusResponse {
  instagram: PlatformStatus;
  xiaohongshu: PlatformStatus;
}

async function importXhsCookies(rawText: string): Promise<void> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    throw new Error("Invalid JSON — paste the full Cookie-Editor export.");
  }
  // Accept both [{...}, ...] and {"cookies": [...]}
  const cookies = Array.isArray(parsed)
    ? parsed
    : (parsed as Record<string, unknown>).cookies;
  if (!Array.isArray(cookies)) throw new Error("No cookie array found in pasted JSON.");
  const res = await fetch("/api/auth/xiaohongshu/cookies", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cookies }),
  });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(data.detail ?? "Cookie import failed.");
  }
}

async function fetchStatus(): Promise<AuthStatusResponse> {
  const res = await fetch("/api/auth/status");
  if (!res.ok) throw new Error("Failed to fetch auth status");
  return res.json() as Promise<AuthStatusResponse>;
}

async function startAuth(platform: string): Promise<void> {
  const res = await fetch(`/api/auth/${platform}`, { method: "POST" });
  if (!res.ok) throw new Error(`Failed to start auth for ${platform}`);
}

export default function Setup() {
  const [status, setStatus] = useState<AuthStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [xhsCookieText, setXhsCookieText] = useState("");
  const [importingCookies, setImportingCookies] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const s = await fetchStatus();
      setStatus(s);

      // Stop polling once both platforms are authenticated and neither is connecting
      const allDone =
        s.instagram.status === "authenticated" &&
        s.xiaohongshu.status === "authenticated" &&
        !s.instagram.connecting &&
        !s.xiaohongshu.connecting;

      if (allDone && pollRef.current !== null) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  // Poll while any platform is connecting
  useEffect(() => {
    if (!status) return;
    const anyConnecting = status.instagram.connecting || status.xiaohongshu.connecting;
    if (anyConnecting && pollRef.current === null) {
      pollRef.current = setInterval(loadStatus, 2000);
    }
    return () => {
      if (pollRef.current !== null) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [status, loadStatus]);

  const handleConnect = async (platform: string) => {
    setError(null);
    try {
      await startAuth(platform);
      // Kick off polling to watch for completion
      await loadStatus();
      if (pollRef.current === null) {
        pollRef.current = setInterval(loadStatus, 2000);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    }
  };

  const handleImportCookies = async () => {
    if (!xhsCookieText.trim()) return;
    setImportingCookies(true);
    setError(null);
    try {
      await importXhsCookies(xhsCookieText);
      setXhsCookieText("");
      await loadStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Cookie import failed.");
    } finally {
      setImportingCookies(false);
    }
  };

  return (
    <div style={styles.page}>
      <div style={styles.card}>
        <h1 style={styles.title}>Inspiration Curator</h1>
        <p style={styles.subtitle}>
          Connect your accounts to start the daily scrape. A browser window will
          open — log in normally, then return here.
        </p>

        {error && <p style={styles.error}>{error}</p>}

        <div style={styles.platformList}>
          {/* Instagram row */}
          {(() => {
            const ps = status?.instagram;
            const authenticated = ps?.status === "authenticated";
            const connecting = ps?.connecting ?? false;
            return (
              <div key="instagram" style={styles.platformRow}>
                <div style={styles.platformInfo}>
                  <span style={styles.platformLabel}>Instagram</span>
                  <StatusBadge authenticated={authenticated} connecting={connecting} />
                </div>
                {!authenticated && (
                  <button
                    style={{ ...styles.button, ...(connecting ? styles.buttonDisabled : {}) }}
                    disabled={connecting}
                    onClick={() => void handleConnect("instagram")}
                  >
                    {connecting ? "Browser opening…" : "Connect Instagram"}
                  </button>
                )}
              </div>
            );
          })()}

          {/* Xiaohongshu row — cookie import instead of browser popup */}
          {(() => {
            const ps = status?.xiaohongshu;
            const authenticated = ps?.status === "authenticated";
            return (
              <div key="xiaohongshu" style={{ ...styles.platformRow, flexDirection: "column", alignItems: "stretch", gap: "0.85rem" }}>
                <div style={styles.platformInfo}>
                  <span style={styles.platformLabel}>小红书 (Xiaohongshu)</span>
                  <StatusBadge authenticated={authenticated} connecting={false} />
                </div>
                <div style={styles.cookieBox}>
                  <p style={styles.cookieHint}>
                    Install{" "}
                    <a href="https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm" target="_blank" rel="noreferrer" style={styles.link}>
                      Cookie-Editor
                    </a>
                    , log in to xiaohongshu.com, then click <strong>Export → Export as JSON</strong> and paste below.
                    {authenticated && " Paste new cookies to re-authenticate."}
                  </p>
                  <textarea
                    style={styles.textarea}
                    placeholder='[{"name":"web_session","value":"..."}]'
                    rows={4}
                    value={xhsCookieText}
                    onChange={(e) => setXhsCookieText(e.target.value)}
                  />
                  <button
                    style={{ ...styles.button, ...(importingCookies || !xhsCookieText.trim() ? styles.buttonDisabled : {}) }}
                    disabled={importingCookies || !xhsCookieText.trim()}
                    onClick={() => void handleImportCookies()}
                  >
                    {importingCookies ? "Importing…" : authenticated ? "Re-authenticate" : "Import Cookies"}
                  </button>
                </div>
              </div>
            );
          })()}
        </div>

        {status?.instagram.status === "authenticated" &&
          status?.xiaohongshu.status === "authenticated" && (
            <p style={styles.allDone}>
              Both platforms connected. You're all set — the daily scrape will
              run automatically.
            </p>
          )}
      </div>
    </div>
  );
}

function StatusBadge({
  authenticated,
  connecting,
}: {
  authenticated: boolean;
  connecting: boolean;
}) {
  if (connecting) {
    return <span style={{ ...styles.badge, ...styles.badgeConnecting }}>Connecting…</span>;
  }
  if (authenticated) {
    return <span style={{ ...styles.badge, ...styles.badgeOk }}>✓ Connected</span>;
  }
  return <span style={{ ...styles.badge, ...styles.badgeMissing }}>Not connected</span>;
}

const styles: Record<string, React.CSSProperties> = {
  page: {
    minHeight: "100vh",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: "2rem",
  },
  card: {
    background: "#1a1a1a",
    borderRadius: "12px",
    padding: "2.5rem",
    maxWidth: "480px",
    width: "100%",
    border: "1px solid #2a2a2a",
  },
  title: {
    margin: "0 0 0.5rem",
    fontSize: "1.6rem",
    fontWeight: 600,
    letterSpacing: "-0.02em",
  },
  subtitle: {
    margin: "0 0 2rem",
    color: "#888",
    fontSize: "0.9rem",
    lineHeight: 1.5,
  },
  error: {
    background: "#2a1010",
    border: "1px solid #5c1a1a",
    borderRadius: "6px",
    padding: "0.75rem 1rem",
    color: "#ff6b6b",
    fontSize: "0.85rem",
    marginBottom: "1.5rem",
  },
  platformList: {
    display: "flex",
    flexDirection: "column",
    gap: "1rem",
  },
  platformRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    background: "#111",
    borderRadius: "8px",
    padding: "1rem 1.25rem",
    border: "1px solid #222",
  },
  platformInfo: {
    display: "flex",
    alignItems: "center",
    gap: "0.75rem",
  },
  platformLabel: {
    fontWeight: 500,
    fontSize: "0.95rem",
  },
  badge: {
    fontSize: "0.75rem",
    padding: "0.2rem 0.6rem",
    borderRadius: "999px",
    fontWeight: 500,
  },
  badgeOk: {
    background: "#0d2b1a",
    color: "#4caf50",
    border: "1px solid #2e7d32",
  },
  badgeMissing: {
    background: "#1e1e1e",
    color: "#666",
    border: "1px solid #333",
  },
  badgeConnecting: {
    background: "#1a1a2e",
    color: "#7986cb",
    border: "1px solid #3949ab",
  },
  button: {
    background: "#e8e8e8",
    color: "#111",
    border: "none",
    borderRadius: "6px",
    padding: "0.5rem 1rem",
    fontSize: "0.85rem",
    fontWeight: 600,
    cursor: "pointer",
    whiteSpace: "nowrap",
  },
  buttonDisabled: {
    background: "#333",
    color: "#666",
    cursor: "not-allowed",
  },
  allDone: {
    marginTop: "1.5rem",
    padding: "0.75rem 1rem",
    background: "#0d2b1a",
    border: "1px solid #2e7d32",
    borderRadius: "6px",
    color: "#4caf50",
    fontSize: "0.85rem",
  },
  cookieBox: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "0.6rem",
  },
  cookieHint: {
    margin: 0,
    color: "#666",
    fontSize: "0.8rem",
    lineHeight: 1.5,
  },
  link: {
    color: "#7986cb",
    textDecoration: "none",
  },
  textarea: {
    background: "#0d0d0d",
    border: "1px solid #2a2a2a",
    borderRadius: "6px",
    color: "#ccc",
    fontSize: "0.78rem",
    fontFamily: "monospace",
    padding: "0.6rem 0.8rem",
    resize: "vertical" as const,
    outline: "none",
    width: "100%",
    boxSizing: "border-box" as const,
  },
};
