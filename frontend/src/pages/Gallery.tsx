import { CSSProperties, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Nav } from "./Today";

interface GalleryPost {
  id: string;
  platform: string;
  creator: string;
  engagement: number;
  screenshot_url: string | null;
}

interface GalleryDay {
  date: string;
  posts: GalleryPost[];
}

export default function Gallery() {
  const [days, setDays] = useState<GalleryDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modal, setModal] = useState<GalleryPost | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch("/api/gallery");
        if (!res.ok) throw new Error("Failed to load gallery");
        setDays((await res.json()) as GalleryDay[]);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    }
    void load();
  }, []);

  // Close modal on Escape
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setModal(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const totalPosts = days.reduce((n, d) => n + d.posts.length, 0);

  return (
    <div style={s.page}>
      <Nav active="gallery" />
      <main style={s.main}>
        <div style={s.pageHeader}>
          <h1 style={s.title}>Gallery</h1>
          {!loading && totalPosts > 0 && (
            <span style={s.count}>{totalPosts} saved</span>
          )}
        </div>

        {error && <div style={s.error}>{error}</div>}
        {loading && <p style={s.empty}>Loading…</p>}

        {!loading && days.length === 0 && (
          <p style={s.empty}>
            No liked posts yet.{" "}
            <Link to="/today" style={s.link}>
              Curate today's posts →
            </Link>
          </p>
        )}

        {days.map((day) => (
          <section key={day.date} style={s.section}>
            <h2 style={s.dateHeader}>{day.date}</h2>
            <div style={s.grid}>
              {day.posts.map((post) => (
                <button
                  key={post.id}
                  style={s.thumb}
                  onClick={() => setModal(post)}
                  title={`@${post.creator}`}
                >
                  {post.screenshot_url ? (
                    <img
                      src={post.screenshot_url}
                      alt={`@${post.creator}`}
                      style={s.thumbImg}
                    />
                  ) : (
                    <div style={s.noThumb}>No image</div>
                  )}
                </button>
              ))}
            </div>
          </section>
        ))}
      </main>

      {modal && (
        <div style={s.overlay} onClick={() => setModal(null)}>
          <div style={s.modalBox} onClick={(e) => e.stopPropagation()}>
            <button style={s.closeBtn} onClick={() => setModal(null)} title="Close (Esc)">
              ✕
            </button>
            {modal.screenshot_url && (
              <div style={s.modalImgWrap}>
                <img
                  src={modal.screenshot_url}
                  alt={`@${modal.creator}`}
                  style={s.modalImg}
                />
              </div>
            )}
            <div style={s.modalMeta}>
              <PlatformBadge platform={modal.platform} />
              <span style={s.modalCreator}>@{modal.creator}</span>
              <span style={s.modalEngagement}>
                {modal.engagement.toLocaleString()} engagements
              </span>
            </div>
          </div>
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
        background: "#222",
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
  page: { minHeight: "100vh", display: "flex", flexDirection: "column" },
  main: { flex: 1, padding: "2.5rem 2rem", maxWidth: 1200, margin: "0 auto", width: "100%" },
  pageHeader: {
    display: "flex",
    alignItems: "baseline",
    justifyContent: "space-between",
    marginBottom: "2.5rem",
  },
  title: { margin: 0, fontSize: "1.5rem", fontWeight: 600, letterSpacing: "-0.02em" },
  count: { color: "#555", fontSize: "0.85rem" },
  error: {
    background: "#2a1010",
    border: "1px solid #5c1a1a",
    borderRadius: 6,
    padding: "0.75rem 1rem",
    color: "#ff6b6b",
    fontSize: "0.85rem",
    marginBottom: "1.5rem",
  },
  empty: { color: "#555", padding: "2rem 0", fontSize: "0.9rem" },
  link: { color: "#888", textDecoration: "none" },
  section: { marginBottom: "3rem" },
  dateHeader: {
    fontSize: "0.78rem",
    fontWeight: 600,
    color: "#555",
    margin: "0 0 1rem",
    textTransform: "uppercase",
    letterSpacing: "0.08em",
  },
  grid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
    gap: "0.6rem",
  },
  thumb: {
    display: "block",
    width: "100%",
    aspectRatio: "1 / 1",
    background: "#111",
    border: "1px solid #1e1e1e",
    borderRadius: 8,
    overflow: "hidden",
    cursor: "pointer",
    padding: 0,
  },
  thumbImg: { width: "100%", height: "100%", objectFit: "cover", display: "block" },
  noThumb: {
    width: "100%",
    height: "100%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "#333",
    fontSize: "0.8rem",
  },
  overlay: {
    position: "fixed",
    inset: 0,
    background: "rgba(0,0,0,0.88)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 100,
    padding: "1.5rem",
  },
  modalBox: {
    background: "#1a1a1a",
    border: "1px solid #2a2a2a",
    borderRadius: 12,
    overflow: "hidden",
    maxWidth: 680,
    width: "100%",
    maxHeight: "92vh",
    display: "flex",
    flexDirection: "column",
    position: "relative",
  },
  closeBtn: {
    position: "absolute",
    top: "0.65rem",
    right: "0.65rem",
    background: "#111",
    border: "1px solid #2a2a2a",
    color: "#888",
    borderRadius: 6,
    width: 30,
    height: 30,
    cursor: "pointer",
    fontSize: "0.8rem",
    zIndex: 1,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  modalImgWrap: {
    flex: 1,
    overflow: "auto",
    background: "#0f0f0f",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  modalImg: { maxWidth: "100%", maxHeight: "78vh", objectFit: "contain", display: "block" },
  modalMeta: {
    display: "flex",
    alignItems: "center",
    gap: "0.75rem",
    padding: "0.75rem 1rem",
    borderTop: "1px solid #222",
    flexShrink: 0,
  },
  modalCreator: { color: "#ccc", fontSize: "0.9rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  modalEngagement: { color: "#555", fontSize: "0.8rem", marginLeft: "auto", whiteSpace: "nowrap" },
};
