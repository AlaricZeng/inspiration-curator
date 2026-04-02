import { CSSProperties, useEffect, useState } from "react";
import { Nav } from "./Today";

interface VibeKeyword {
  keyword: string;
  frequency: number;
  user_pinned: boolean;
  user_blocked: boolean;
}

interface Creator {
  id: string;
  platform: string;
  handle: string;
  liked_count: number;
}

interface TasteData {
  keywords: VibeKeyword[];
  creators: Creator[];
}

export default function TasteProfile() {
  const [data, setData] = useState<TasteData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [addInput, setAddInput] = useState("");
  const [adding, setAdding] = useState(false);

  const load = async () => {
    try {
      const res = await fetch("/api/taste");
      if (!res.ok) throw new Error("Failed to load taste profile");
      setData((await res.json()) as TasteData);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const patchKeyword = async (keyword: string, patch: { pinned?: boolean; blocked?: boolean; add?: boolean }) => {
    try {
      const res = await fetch("/api/taste/keywords", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keyword, ...patch }),
      });
      if (!res.ok) throw new Error("Failed to update keyword");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    }
  };

  const deleteCreator = async (id: string) => {
    try {
      const res = await fetch(`/api/creators/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete creator");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    }
  };

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!addInput.trim()) return;
    setAdding(true);
    setError(null);
    await patchKeyword(addInput.trim(), { add: true });
    setAddInput("");
    setAdding(false);
  };

  const pinned = data?.keywords.filter((k) => k.user_pinned) ?? [];
  const active = data?.keywords.filter((k) => !k.user_pinned && !k.user_blocked) ?? [];
  const blocked = data?.keywords.filter((k) => k.user_blocked) ?? [];

  return (
    <div style={s.page}>
      <Nav active="taste-profile" />
      <main style={s.main}>
        <div style={s.container}>
          <h1 style={s.title}>Taste Profile</h1>
          <p style={s.subtitle}>Your evolving aesthetic — built from every post you've liked.</p>

          {error && <div style={s.error}>{error}</div>}

          {/* Keywords section */}
          <section style={s.section}>
            <h2 style={s.sectionTitle}>Vibe Keywords</h2>

            {data && data.keywords.length === 0 && (
              <p style={s.empty}>No keywords yet. Like some posts to build your profile, or pick a style preset on the Today page.</p>
            )}

            {pinned.length > 0 && (
              <div style={s.group}>
                <span style={s.groupLabel}>Pinned</span>
                <div style={s.tagCloud}>
                  {pinned.map((kw) => (
                    <KeywordTag
                      key={kw.keyword}
                      kw={kw}
                      onPin={() => void patchKeyword(kw.keyword, { pinned: !kw.user_pinned })}
                      onBlock={() => void patchKeyword(kw.keyword, { blocked: true })}
                    />
                  ))}
                </div>
              </div>
            )}

            {active.length > 0 && (
              <div style={s.group}>
                <span style={s.groupLabel}>Active</span>
                <div style={s.tagCloud}>
                  {active.map((kw) => (
                    <KeywordTag
                      key={kw.keyword}
                      kw={kw}
                      onPin={() => void patchKeyword(kw.keyword, { pinned: true })}
                      onBlock={() => void patchKeyword(kw.keyword, { blocked: true })}
                    />
                  ))}
                </div>
              </div>
            )}

            {blocked.length > 0 && (
              <div style={s.group}>
                <span style={s.groupLabel}>Blocked</span>
                <div style={s.tagCloud}>
                  {blocked.map((kw) => (
                    <KeywordTag
                      key={kw.keyword}
                      kw={kw}
                      onPin={() => void patchKeyword(kw.keyword, { pinned: false, blocked: false })}
                      onBlock={() => void patchKeyword(kw.keyword, { blocked: false })}
                      isBlocked
                    />
                  ))}
                </div>
              </div>
            )}

            <form onSubmit={(e) => void handleAdd(e)} style={s.addForm}>
              <input
                style={s.input}
                type="text"
                placeholder="Add keyword…"
                value={addInput}
                onChange={(e) => setAddInput(e.target.value)}
              />
              <button
                type="submit"
                style={{ ...s.btn, ...(adding || !addInput.trim() ? s.btnDisabled : {}) }}
                disabled={adding || !addInput.trim()}
              >
                {adding ? "Adding…" : "Add"}
              </button>
            </form>
          </section>

          {/* Creators section */}
          <section style={s.section}>
            <h2 style={s.sectionTitle}>Tracked Creators</h2>
            {data && data.creators.length === 0 && (
              <p style={s.empty}>No creators tracked yet. Creators are added automatically when you like their posts.</p>
            )}
            {data && data.creators.length > 0 && (
              <div style={s.creatorList}>
                {data.creators.map((c) => (
                  <div key={c.id} style={s.creatorRow}>
                    <PlatformBadge platform={c.platform} />
                    <span style={s.creatorHandle}>{c.handle}</span>
                    <span style={s.likedCount}>{c.liked_count} liked</span>
                    <button
                      style={s.deleteBtn}
                      onClick={() => void deleteCreator(c.id)}
                      title="Remove creator"
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}

function KeywordTag({
  kw,
  onPin,
  onBlock,
  isBlocked = false,
}: {
  kw: VibeKeyword;
  onPin: () => void;
  onBlock: () => void;
  isBlocked?: boolean;
}) {
  const tagStyle: CSSProperties = {
    ...s.tag,
    ...(kw.user_pinned ? s.tagPinned : {}),
    ...(isBlocked ? s.tagBlocked : {}),
  };
  return (
    <div style={tagStyle}>
      <span style={isBlocked ? s.tagTextBlocked : s.tagText}>{kw.keyword}</span>
      <span style={s.freq}>{kw.frequency}</span>
      <button
        style={{ ...s.tagBtn, ...(kw.user_pinned ? s.tagBtnActive : {}) }}
        onClick={onPin}
        title={isBlocked ? "Unblock" : kw.user_pinned ? "Unpin" : "Pin"}
      >
        {isBlocked ? "↩" : kw.user_pinned ? "★" : "☆"}
      </button>
      <button
        style={{ ...s.tagBtn, ...(isBlocked ? s.tagBtnDanger : {}) }}
        onClick={onBlock}
        title={isBlocked ? "Remove block" : "Block"}
      >
        {isBlocked ? "✓" : "✕"}
      </button>
    </div>
  );
}

function PlatformBadge({ platform }: { platform: string }) {
  const labels: Record<string, string> = {
    instagram: "IG",
    xiaohongshu: "RED",
  };
  const colors: Record<string, { bg: string; color: string }> = {
    instagram: { bg: "#2a1a2e", color: "#ce93d8" },
    xiaohongshu: { bg: "#2a1010", color: "#ef9a9a" },
  };
  const c = colors[platform] ?? { bg: "#1a1a1a", color: "#888" };
  return (
    <span
      style={{
        background: c.bg,
        color: c.color,
        borderRadius: 4,
        padding: "0.15rem 0.4rem",
        fontSize: "0.7rem",
        fontWeight: 700,
        letterSpacing: "0.04em",
        flexShrink: 0,
      }}
    >
      {labels[platform] ?? platform}
    </span>
  );
}

const s: Record<string, CSSProperties> = {
  page: { minHeight: "100vh", display: "flex", flexDirection: "column" },
  main: {
    flex: 1,
    display: "flex",
    justifyContent: "center",
    padding: "3rem 2rem",
  },
  container: { width: "100%", maxWidth: 680 },
  title: { margin: "0 0 0.25rem", fontSize: "1.6rem", fontWeight: 600, letterSpacing: "-0.02em" },
  subtitle: { color: "#555", fontSize: "0.85rem", margin: "0 0 2.5rem" },
  error: {
    background: "#2a1010",
    border: "1px solid #5c1a1a",
    borderRadius: 6,
    padding: "0.75rem 1rem",
    color: "#ff6b6b",
    fontSize: "0.85rem",
    marginBottom: "1.5rem",
  },
  section: { marginBottom: "2.5rem" },
  sectionTitle: {
    fontSize: "0.75rem",
    fontWeight: 700,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    color: "#555",
    margin: "0 0 1rem",
  },
  empty: { color: "#444", fontSize: "0.85rem", fontStyle: "italic" },
  group: { marginBottom: "1rem" },
  groupLabel: {
    fontSize: "0.7rem",
    fontWeight: 600,
    letterSpacing: "0.06em",
    textTransform: "uppercase" as const,
    color: "#444",
    display: "block",
    marginBottom: "0.5rem",
  },
  tagCloud: { display: "flex", flexWrap: "wrap" as const, gap: "0.5rem" },
  tag: {
    display: "inline-flex",
    alignItems: "center",
    gap: "0.35rem",
    background: "#1a1a1a",
    border: "1px solid #2a2a2a",
    borderRadius: 999,
    padding: "0.3rem 0.6rem",
    fontSize: "0.82rem",
  },
  tagPinned: {
    background: "#0d2b1a",
    border: "1px solid #2e7d32",
  },
  tagBlocked: {
    background: "#111",
    border: "1px solid #222",
    opacity: 0.6,
  },
  tagText: { color: "#ccc" },
  tagTextBlocked: { color: "#555", textDecoration: "line-through" },
  freq: {
    color: "#444",
    fontSize: "0.72rem",
    minWidth: "1rem",
    textAlign: "center" as const,
  },
  tagBtn: {
    background: "transparent",
    border: "none",
    cursor: "pointer",
    color: "#444",
    padding: 0,
    fontSize: "0.8rem",
    lineHeight: 1,
  },
  tagBtnActive: { color: "#4caf50" },
  tagBtnDanger: { color: "#4caf50" },
  addForm: { display: "flex", gap: "0.5rem", marginTop: "1rem" },
  input: {
    flex: 1,
    background: "#111",
    border: "1px solid #2a2a2a",
    borderRadius: 6,
    padding: "0.5rem 0.85rem",
    color: "#e8e8e8",
    fontSize: "0.88rem",
    outline: "none",
  },
  btn: {
    background: "#e8e8e8",
    color: "#111",
    border: "none",
    borderRadius: 6,
    padding: "0.5rem 1rem",
    fontSize: "0.85rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  btnDisabled: { background: "#1e1e1e", color: "#444", cursor: "not-allowed" },
  creatorList: { display: "flex", flexDirection: "column" as const, gap: "0.5rem" },
  creatorRow: {
    display: "flex",
    alignItems: "center",
    gap: "0.65rem",
    background: "#1a1a1a",
    border: "1px solid #2a2a2a",
    borderRadius: 8,
    padding: "0.6rem 0.85rem",
  },
  creatorHandle: { flex: 1, color: "#ccc", fontSize: "0.88rem" },
  likedCount: { color: "#444", fontSize: "0.78rem" },
  deleteBtn: {
    background: "transparent",
    border: "none",
    cursor: "pointer",
    color: "#444",
    fontSize: "1.1rem",
    lineHeight: 1,
    padding: "0 0.2rem",
  },
};
