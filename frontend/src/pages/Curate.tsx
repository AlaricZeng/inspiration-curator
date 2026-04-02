import { CSSProperties, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

interface PostOut {
  id: string;
  platform: string;
  creator: string;
  engagement: number;
  screenshot_url: string | null;
  status: string;
}

export default function Curate() {
  const [posts, setPosts] = useState<PostOut[]>([]);
  const [index, setIndex] = useState(0);
  const [likedCount, setLikedCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch("/api/today");
        if (!res.ok) throw new Error("Failed to fetch today's posts");
        const data = (await res.json()) as { posts: PostOut[] };
        setPosts(data.posts.filter((p) => p.status === "pending"));
      } catch (e) {
        setError(e instanceof Error ? e.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    }
    void load();
  }, []);

  const done = index >= posts.length;
  const current = done ? null : posts[index];
  const total = posts.length;

  const handleLike = useCallback(async () => {
    if (!current || acting) return;
    setActing(true);
    setError(null);
    try {
      const res = await fetch(`/api/posts/${current.id}/like`, { method: "POST" });
      if (!res.ok) throw new Error("Failed to like post");
      setLikedCount((c) => c + 1);
      setIndex((i) => i + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setActing(false);
    }
  }, [current, acting]);

  const handleSkip = useCallback(async () => {
    if (!current || acting) return;
    setActing(true);
    setError(null);
    try {
      const res = await fetch(`/api/posts/${current.id}/skip`, { method: "POST" });
      if (!res.ok) throw new Error("Failed to skip post");
      setIndex((i) => i + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setActing(false);
    }
  }, [current, acting]);

  // Keyboard shortcuts: → / L = like, ← / S = skip
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight" || e.key === "l" || e.key === "L") {
        void handleLike();
      } else if (e.key === "ArrowLeft" || e.key === "s" || e.key === "S") {
        void handleSkip();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handleLike, handleSkip]);

  if (loading) {
    return <div style={s.centred}>Loading posts…</div>;
  }

  if (error && total === 0) {
    return (
      <div style={s.centred}>
        <p style={{ color: "#ff6b6b" }}>{error}</p>
        <Link to="/today" style={s.linkPlain}>← Back to Today</Link>
      </div>
    );
  }

  if (done) {
    return (
      <div style={s.page}>
        <header style={s.header}>
          <Link to="/today" style={s.back}>← Today</Link>
        </header>
        <div style={s.summary}>
          {total === 0 ? (
            <>
              <h2 style={s.summaryTitle}>No posts to curate</h2>
              <p style={s.summaryBody}>Run the daily scrape first to get posts to review.</p>
              <Link to="/today" style={s.summaryBtn}>← Back to Today</Link>
            </>
          ) : (
            <>
              <h2 style={s.summaryTitle}>Curation complete</h2>
              <p style={s.summaryBody}>
                You liked <strong style={{ color: "#4caf50" }}>{likedCount}</strong> out of{" "}
                <strong>{total}</strong> posts.
              </p>
              <Link to="/gallery" style={s.summaryBtn}>View Gallery →</Link>
              <Link to="/today" style={s.linkPlain}>← Back to Today</Link>
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div style={s.page}>
      <header style={s.header}>
        <Link to="/today" style={s.back}>← Today</Link>
        <span style={s.progress}>
          {index + 1} / {total}
        </span>
      </header>

      {error && <div style={s.error}>{error}</div>}

      {current && (
        <div style={s.card}>
          <div style={s.imageWrap}>
            {current.screenshot_url ? (
              <img
                src={current.screenshot_url}
                alt={`Post by @${current.creator}`}
                style={s.image}
              />
            ) : (
              <div style={s.noImage}>No screenshot available</div>
            )}
          </div>

          <div style={s.meta}>
            <PlatformBadge platform={current.platform} />
            <span style={s.creator}>@{current.creator}</span>
            <span style={s.engagement}>{current.engagement.toLocaleString()} engagements</span>
          </div>

          <div style={s.actions}>
            <button
              style={{ ...s.btn, ...s.btnSkip, ...(acting ? s.btnDisabled : {}) }}
              onClick={() => void handleSkip()}
              disabled={acting}
              title="Skip (← or S)"
            >
              ✕ &nbsp;Skip
            </button>
            <button
              style={{ ...s.btn, ...s.btnLike, ...(acting ? s.btnDisabled : {}) }}
              onClick={() => void handleLike()}
              disabled={acting}
              title="Like (→ or L)"
            >
              ♥ &nbsp;Like
            </button>
          </div>

          <p style={s.shortcuts}>← S &nbsp;·&nbsp; skip &nbsp;&nbsp;&nbsp; like &nbsp;·&nbsp; → L</p>
        </div>
      )}
    </div>
  );
}

function PlatformBadge({ platform }: { platform: string }) {
  const isRed = platform === "xiaohongshu";
  const label = isRed ? "小红书" : "Instagram";
  const color = isRed ? "#ff2442" : "#e1306c";
  return (
    <span
      style={{
        background: "#111",
        color,
        border: `1px solid ${color}55`,
        borderRadius: 4,
        padding: "0.15rem 0.5rem",
        fontSize: "0.75rem",
        fontWeight: 600,
        flexShrink: 0,
      }}
    >
      {label}
    </span>
  );
}

const s: Record<string, CSSProperties> = {
  page: { minHeight: "100vh", display: "flex", flexDirection: "column", alignItems: "center" },
  centred: {
    minHeight: "100vh",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    color: "#888",
    gap: "1rem",
  },
  header: {
    width: "100%",
    maxWidth: 720,
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "1rem 1.5rem",
  },
  back: { color: "#666", textDecoration: "none", fontSize: "0.85rem" },
  progress: { color: "#aaa", fontSize: "0.9rem", fontWeight: 600 },
  error: {
    background: "#2a1010",
    border: "1px solid #5c1a1a",
    borderRadius: 6,
    padding: "0.5rem 1rem",
    color: "#ff6b6b",
    fontSize: "0.85rem",
    margin: "0 1.5rem 1rem",
    maxWidth: 720,
    width: "calc(100% - 3rem)",
  },
  card: { width: "100%", maxWidth: 720, padding: "0 1.5rem 2.5rem" },
  imageWrap: {
    background: "#111",
    borderRadius: 10,
    overflow: "hidden",
    marginBottom: "1rem",
    border: "1px solid #1e1e1e",
  },
  image: { width: "100%", height: "auto", display: "block" },
  noImage: {
    height: 420,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "#444",
    fontSize: "0.9rem",
  },
  meta: {
    display: "flex",
    alignItems: "center",
    gap: "0.6rem",
    marginBottom: "1rem",
    padding: "0 0.1rem",
    overflow: "hidden",
  },
  creator: { color: "#bbb", fontSize: "0.9rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  engagement: { color: "#555", fontSize: "0.8rem", marginLeft: "auto", whiteSpace: "nowrap" },
  actions: { display: "flex", gap: "0.75rem" },
  btn: {
    flex: 1,
    padding: "0.9rem",
    borderRadius: 8,
    fontSize: "1rem",
    fontWeight: 600,
    cursor: "pointer",
    border: "none",
  },
  btnSkip: { background: "#1e1e1e", color: "#888", border: "1px solid #2a2a2a" },
  btnLike: { background: "#0d2b1a", color: "#4caf50", border: "1px solid #2e7d32" },
  btnDisabled: { opacity: 0.35, cursor: "not-allowed" },
  shortcuts: {
    textAlign: "center",
    color: "#383838",
    fontSize: "0.75rem",
    marginTop: "0.6rem",
    letterSpacing: "0.02em",
  },
  summary: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    padding: "5rem 2rem",
    textAlign: "center",
    maxWidth: 380,
    gap: "0.5rem",
  },
  summaryTitle: {
    margin: "0 0 0.5rem",
    fontSize: "1.5rem",
    fontWeight: 600,
    letterSpacing: "-0.02em",
  },
  summaryBody: { color: "#888", fontSize: "1rem", margin: "0 0 1.5rem" },
  summaryBtn: {
    background: "#e8e8e8",
    color: "#111",
    textDecoration: "none",
    borderRadius: 6,
    padding: "0.6rem 1.4rem",
    fontWeight: 600,
    fontSize: "0.9rem",
    marginBottom: "0.5rem",
  },
  linkPlain: { color: "#555", fontSize: "0.85rem", textDecoration: "none" },
};
