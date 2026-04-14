import { CSSProperties, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

const PRESETS = [
  "Street Photography",
  "Architecture",
  "Portrait",
  "Nature",
  "Minimal Design",
  "Film",
  "Fashion",
] as const;

interface PlatformProgress {
  status: "pending" | "running" | "done" | "skipped";
  post_count: number;
}

interface TodayData {
  date: string;
  status: "pending" | "running" | "done" | "failed";
  keyword: string | null;
  pending_count: number;
  instagram: PlatformProgress | null;
  xiaohongshu: PlatformProgress | null;
}

export default function Today() {
  const [data, setData] = useState<TodayData | null>(null);
  const [keywordInput, setKeywordInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [clearing, setClearing] = useState(false);
  const keywordInitRef = useRef(false);
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [seedNeeded, setSeedNeeded] = useState(false);
  const [seedingPreset, setSeedingPreset] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const checkSeedNeeded = useCallback(async () => {
    try {
      const res = await fetch("/api/taste/seed-needed");
      if (res.ok) {
        const d = (await res.json()) as { seed_needed: boolean };
        setSeedNeeded(d.seed_needed);
      }
    } catch {
      // non-critical — ignore
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/today");
      if (!res.ok) throw new Error("Failed to fetch today's status");
      const d = (await res.json()) as TodayData;
      setData(d);
      if (!keywordInitRef.current) {
        setKeywordInput(d.keyword ?? "");
        keywordInitRef.current = true;
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    }
  }, []);

  useEffect(() => {
    void load();
    void checkSeedNeeded();
  }, [load, checkSeedNeeded]);

  // Refetch when the user returns to this tab (e.g. after curating some posts)
  useEffect(() => {
    const onVisible = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [load]);

  // Poll every 10s while status is "running"
  useEffect(() => {
    const isRunning = data?.status === "running";
    if (isRunning && !pollRef.current) {
      pollRef.current = setInterval(() => void load(), 10_000);
    } else if (!isRunning && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [data?.status, load]);

  const handleSaveKeyword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!keywordInput.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const res = await fetch("/api/today/keyword", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keyword: keywordInput.trim() }),
      });
      if (!res.ok) throw new Error("Failed to save keyword");
      setKeywordInput("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setSaving(false);
    }
  };

  const handleClearKeyword = async () => {
    setClearing(true);
    setError(null);
    try {
      const res = await fetch("/api/today/keyword", { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to clear keyword");
      setKeywordInput("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setClearing(false);
    }
  };

  const handleRunNow = async () => {
    setLaunching(true);
    setError(null);
    try {
      const res = await fetch("/api/run/now", { method: "POST" });
      if (!res.ok) throw new Error("Failed to trigger run");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLaunching(false);
    }
  };

  const handleSeedPreset = async (preset: string) => {
    setSeedingPreset(preset);
    setError(null);
    try {
      const res = await fetch("/api/taste/seed", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preset }),
      });
      if (!res.ok) throw new Error("Failed to seed preset");
      setSeedNeeded(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setSeedingPreset(null);
    }
  };

  const isRunning = data?.status === "running" || launching;

  return (
    <div style={s.page}>
      <Nav active="today" />
      <main style={s.main}>
        <div style={s.card}>
          <div style={s.cardHeader}>
            <h1 style={s.title}>Today</h1>
            {data && <span style={s.date}>{data.date}</span>}
          </div>

          {error && <div style={s.error}>{error}</div>}

          {data && (
            <div style={s.statusRow}>
              <span style={s.statusLabel}>Status</span>
              <StatusChip status={data.status} />
            </div>
          )}

          {data && (data.instagram || data.xiaohongshu) && (
            <RunProgress instagram={data.instagram ?? null} xiaohongshu={data.xiaohongshu ?? null} />
          )}

          {seedNeeded && (
            <div style={s.seedBox}>
              <p style={s.seedTitle}>Pick a starting style</p>
              <p style={s.seedHint}>Seeds your taste profile so vibe mode has something to work with. Disappears once you've liked 3 posts.</p>
              <div style={s.presetGrid}>
                {PRESETS.map((p) => (
                  <button
                    key={p}
                    style={{
                      ...s.presetBtn,
                      ...(seedingPreset === p ? s.btnDisabled : {}),
                    }}
                    disabled={seedingPreset !== null}
                    onClick={() => void handleSeedPreset(p)}
                  >
                    {seedingPreset === p ? "…" : p}
                  </button>
                ))}
              </div>
            </div>
          )}

          <form onSubmit={(e) => void handleSaveKeyword(e)} style={s.form}>
            <label style={s.label}>
              Search keyword{data?.keyword ? <span style={s.keywordSaved}> — {data.keyword}</span> : null}
            </label>
            <div style={s.row}>
              <input
                style={s.input}
                type="text"
                placeholder="e.g. Japan cherry blossom"
                value={keywordInput}
                onChange={(e) => setKeywordInput(e.target.value)}
              />
              <button
                style={{ ...s.btn, ...s.btnSecondary, ...(saving || !keywordInput.trim() ? s.btnDisabled : {}) }}
                type="submit"
                disabled={saving || !keywordInput.trim()}
              >
                {saving ? "…" : "Save"}
              </button>
              <button
                type="button"
                style={{ ...s.btn, ...s.btnDanger, ...(!data?.keyword || clearing ? s.btnDisabled : {}) }}
                onClick={() => void handleClearKeyword()}
                disabled={!data?.keyword || clearing}
              >
                {clearing ? "…" : "Clear"}
              </button>
            </div>
            <p style={s.hint}>
              {data?.keyword ? "Saved — all scrapes will use this keyword until cleared." : "Leave blank to use your taste profile."}
            </p>
          </form>

          <div style={s.actions}>
            <button
              style={{ ...s.btn, ...s.btnSecondary, ...(isRunning ? s.btnDisabled : {}) }}
              onClick={() => void handleRunNow()}
              disabled={isRunning}
            >
              {isRunning ? "Running…" : "Run Now"}
            </button>

            {(data?.pending_count ?? 0) > 0 && (
              <Link to="/curate" style={{ ...s.btn, ...s.btnAccent, textDecoration: "none" }}>
                Review — {data!.pending_count}
              </Link>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

function PlatformBar({ label, progress }: { label: string; progress: PlatformProgress | null }) {
  const isDone = progress?.status === "done";
  const isSkipped = progress?.status === "skipped";

  let statusText = "waiting…";
  if (isDone) statusText = `${progress!.post_count} posts`;
  else if (isSkipped) statusText = "skipped";
  else if (progress?.status === "running" || progress == null) statusText = "fetching…";

  return (
    <div style={rp.platformRow}>
      <div style={rp.platformLabel}>
        <span style={rp.platformName}>{label}</span>
        <span style={{ ...rp.platformStatus, color: isDone ? "#4caf50" : isSkipped ? "#555" : "#7986cb" }}>
          {statusText}
        </span>
      </div>
      <div style={rp.track}>
        {isDone || isSkipped ? (
          <div style={{ ...rp.bar, width: "100%", animation: "none", background: isDone ? "#4caf50" : "#2e2e2e", opacity: isDone ? 1 : 0.4 }} />
        ) : (
          <div style={rp.bar} />
        )}
      </div>
    </div>
  );
}

function RunProgress({ instagram, xiaohongshu }: {
  instagram: PlatformProgress | null;
  xiaohongshu: PlatformProgress | null;
}) {
  return (
    <div style={rp.wrap}>
      <style>{`
        @keyframes shimmer {
          0%   { transform: translateX(-100%); }
          100% { transform: translateX(250%); }
        }
      `}</style>
      <PlatformBar label="Instagram" progress={instagram} />
      <PlatformBar label="Red (小红书)" progress={xiaohongshu} />
    </div>
  );
}

const rp: Record<string, CSSProperties> = {
  wrap: { marginBottom: "1.5rem", display: "flex", flexDirection: "column", gap: "0.75rem" },
  platformRow: { display: "flex", flexDirection: "column", gap: "0.3rem" },
  platformLabel: { display: "flex", justifyContent: "space-between", alignItems: "baseline" },
  platformName: { color: "#888", fontSize: "0.78rem", fontWeight: 600 },
  platformStatus: { fontSize: "0.72rem" },
  track: {
    height: 4,
    background: "#1e1e1e",
    borderRadius: 999,
    overflow: "hidden",
  },
  bar: {
    height: "100%",
    width: "40%",
    background: "linear-gradient(90deg, transparent, #7986cb, transparent)",
    borderRadius: 999,
    animation: "shimmer 1.6s ease-in-out infinite",
  },
};

function StatusChip({ status }: { status: string }) {
  const palette: Record<string, { bg: string; color: string; border: string }> = {
    pending: { bg: "#1a1a1a", color: "#777", border: "#2e2e2e" },
    running: { bg: "#1a1a2e", color: "#7986cb", border: "#3949ab" },
    done: { bg: "#0d2b1a", color: "#4caf50", border: "#2e7d32" },
    failed: { bg: "#2a1010", color: "#ef5350", border: "#b71c1c" },
  };
  const c = palette[status] ?? palette.pending;
  return (
    <span
      style={{
        background: c.bg,
        color: c.color,
        border: `1px solid ${c.border}`,
        borderRadius: 999,
        padding: "0.2rem 0.7rem",
        fontSize: "0.78rem",
        fontWeight: 600,
      }}
    >
      {status}
    </span>
  );
}

export function Nav({ active }: { active: string }) {
  const links: { to: string; label: string; key: string }[] = [
    { to: "/today", label: "Today", key: "today" },
    { to: "/gallery", label: "Gallery", key: "gallery" },
    { to: "/taste-profile", label: "Taste", key: "taste-profile" },
    { to: "/setup", label: "Setup", key: "setup" },
  ];
  return (
    <nav style={s.nav}>
      <span style={s.navBrand}>Inspiration</span>
      {links.map((l) => (
        <Link
          key={l.key}
          to={l.to}
          style={{ ...s.navLink, ...(active === l.key ? s.navLinkActive : {}) }}
        >
          {l.label}
        </Link>
      ))}
    </nav>
  );
}

const s: Record<string, CSSProperties> = {
  page: { minHeight: "100vh", display: "flex", flexDirection: "column" },
  nav: {
    display: "flex",
    alignItems: "center",
    gap: "1.5rem",
    padding: "0.85rem 2rem",
    borderBottom: "1px solid #222",
    background: "#111",
  },
  navBrand: {
    fontWeight: 700,
    fontSize: "0.9rem",
    marginRight: "auto",
    letterSpacing: "-0.01em",
    color: "#e8e8e8",
  },
  navLink: { color: "#666", textDecoration: "none", fontSize: "0.85rem" },
  navLinkActive: { color: "#e8e8e8" },
  main: {
    flex: 1,
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "center",
    padding: "3rem 2rem",
  },
  card: {
    background: "#1a1a1a",
    border: "1px solid #2a2a2a",
    borderRadius: 12,
    padding: "2rem",
    width: "100%",
    maxWidth: 520,
  },
  cardHeader: {
    display: "flex",
    alignItems: "baseline",
    justifyContent: "space-between",
    marginBottom: "1.5rem",
  },
  title: { margin: 0, fontSize: "1.5rem", fontWeight: 600, letterSpacing: "-0.02em" },
  date: { color: "#555", fontSize: "0.85rem" },
  error: {
    background: "#2a1010",
    border: "1px solid #5c1a1a",
    borderRadius: 6,
    padding: "0.75rem 1rem",
    color: "#ff6b6b",
    fontSize: "0.85rem",
    marginBottom: "1.25rem",
  },
  statusRow: {
    display: "inline-flex",
    alignItems: "center",
    gap: "0.75rem",
    marginBottom: "1.5rem",
    padding: "0.4rem 0.85rem",
    background: "#111",
    borderRadius: 8,
    border: "1px solid #222",
  },
  statusLabel: { color: "#666", fontSize: "0.85rem" },
  form: { marginBottom: "1.5rem" },
  label: { display: "block", color: "#888", fontSize: "0.8rem", marginBottom: "0.5rem" },
  row: { display: "flex", gap: "0.5rem" },
  input: {
    flex: 1,
    background: "#111",
    border: "1px solid #2a2a2a",
    borderRadius: 6,
    padding: "0.55rem 0.9rem",
    color: "#e8e8e8",
    fontSize: "0.9rem",
    outline: "none",
  },
  hint: { color: "#444", fontSize: "0.75rem", margin: "0.4rem 0 0" },
  actions: { display: "flex", gap: "0.75rem", flexWrap: "wrap", alignItems: "center" },
  btn: {
    background: "#e8e8e8",
    color: "#111",
    border: "none",
    borderRadius: 6,
    padding: "0.55rem 1.1rem",
    fontSize: "0.85rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  btnSecondary: { background: "#252525", color: "#ccc", border: "1px solid #333" },
  btnDanger: { background: "#2a1010", color: "#ef5350", border: "1px solid #5c1a1a", flexShrink: 0 },
  keywordSaved: { color: "#7986cb", fontWeight: 400 },
  btnAccent: {
    background: "#0d2b1a",
    color: "#4caf50",
    border: "1px solid #2e7d32",
    display: "inline-block",
  },
  btnDisabled: { background: "#1e1e1e", color: "#444", cursor: "not-allowed", border: "1px solid #2a2a2a" },
  seedBox: {
    background: "#111",
    border: "1px solid #2a2a2a",
    borderRadius: 8,
    padding: "1rem",
    marginBottom: "1.5rem",
  },
  seedTitle: { margin: "0 0 0.25rem", color: "#ccc", fontSize: "0.88rem", fontWeight: 600 },
  seedHint: { margin: "0 0 0.85rem", color: "#555", fontSize: "0.75rem" },
  presetGrid: { display: "flex", flexWrap: "wrap" as const, gap: "0.4rem" },
  presetBtn: {
    background: "#1a1a1a",
    color: "#aaa",
    border: "1px solid #2e2e2e",
    borderRadius: 6,
    padding: "0.35rem 0.75rem",
    fontSize: "0.8rem",
    cursor: "pointer",
  },
};
