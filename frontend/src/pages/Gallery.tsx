import { CSSProperties, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Nav } from "./Today";

interface GalleryPost {
  id: string;
  platform: string;
  creator: string;
  engagement: number;
  screenshot_url: string | null;
  date: string;
  keyword: string | null;
  run_mode: string;
  vibe_keywords: string[] | null;
  tags: string[] | null;
}

interface GalleryDay {
  date: string;
  posts: GalleryPost[];
}

type GroupBy = "date" | "keyword" | "vibe" | "tags";

export default function Gallery() {
  const [days, setDays] = useState<GalleryDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modalIdx, setModalIdx] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [groupBy, setGroupBy] = useState<GroupBy>("date");
  const [selectedKeywords, setSelectedKeywords] = useState<Set<string>>(new Set());

  const allPosts = days.flatMap((d) => d.posts);

  // --- Vibe mode: keyword filter ---
  const keywordFreq = new Map<string, number>();
  for (const post of allPosts) {
    for (const kw of post.vibe_keywords ?? []) {
      keywordFreq.set(kw, (keywordFreq.get(kw) ?? 0) + 1);
    }
  }
  const allVibeKeywords = Array.from(keywordFreq.entries())
    .sort((a, b) => b[1] - a[1])
    .map(([kw]) => kw);

  const vibePosts =
    selectedKeywords.size === 0
      ? allPosts
      : allPosts.filter((p) => p.vibe_keywords?.some((kw) => selectedKeywords.has(kw)));

  // --- Tags mode: scraped hashtag filter ---
  const tagFreq = new Map<string, number>();
  for (const post of allPosts) {
    for (const tag of post.tags ?? []) {
      tagFreq.set(tag, (tagFreq.get(tag) ?? 0) + 1);
    }
  }
  const allTags = Array.from(tagFreq.entries())
    .sort((a, b) => b[1] - a[1])
    .map(([tag]) => tag);

  const tagPosts =
    selectedKeywords.size === 0
      ? allPosts
      : allPosts.filter((p) => p.tags?.some((tag) => selectedKeywords.has(tag)));

  const toggleKeyword = (kw: string) => {
    setSelectedKeywords((prev) => {
      const next = new Set(prev);
      if (next.has(kw)) next.delete(kw);
      else next.add(kw);
      setModalIdx(null);
      return next;
    });
  };

  // --- Date / Keyword mode: grouped sections ---
  const groups: { label: string; posts: GalleryPost[] }[] = (() => {
    if (groupBy === "vibe") return [];
    const map = new Map<string, GalleryPost[]>();
    for (const post of allPosts) {
      const key =
        groupBy === "keyword"
          ? (post.run_mode === "keyword" && post.keyword ? post.keyword.toLowerCase() : "")
          : post.date;
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(post);
    }
    const entries = Array.from(map.entries()).sort(([a], [b]) => {
      if (a === "" && b !== "") return 1;
      if (a !== "" && b === "") return -1;
      if (groupBy === "date") return b.localeCompare(a); // newest first
      return a.localeCompare(b);
    });
    return entries.map(([key, posts]) => ({
      label: key === "" ? (groupBy === "keyword" ? "Vibe / No keyword" : "Unknown") : key,
      posts,
    }));
  })();

  // Modal operates over the active filtered set
  const modalPosts =
    groupBy === "vibe" ? vibePosts : groupBy === "tags" ? tagPosts : allPosts;
  const modal = modalIdx !== null ? (modalPosts[modalIdx] ?? null) : null;

  const openModal = (post: GalleryPost) =>
    setModalIdx(modalPosts.findIndex((p) => p.id === post.id));
  const prev = () => setModalIdx((i) => (i !== null && i > 0 ? i - 1 : i));
  const next = () =>
    setModalIdx((i) => (i !== null && i < modalPosts.length - 1 ? i + 1 : i));

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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setModalIdx(null);
      if (e.key === "ArrowLeft") prev();
      if (e.key === "ArrowRight") next();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const handleDelete = async (post: GalleryPost) => {
    if (!window.confirm(`Delete post by @${post.creator}? This can't be undone.`)) return;
    setDeleting(true);
    try {
      const res = await fetch(`/api/gallery/${post.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete post");
      setDays((prev) =>
        prev
          .map((day) => ({ ...day, posts: day.posts.filter((p) => p.id !== post.id) }))
          .filter((day) => day.posts.length > 0)
      );
      setModalIdx(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setDeleting(false);
    }
  };

  const totalPosts = days.reduce((n, d) => n + d.posts.length, 0);

  return (
    <div style={s.page}>
      <Nav active="gallery" />
      <main style={s.main}>
        <div style={s.pageHeader}>
          <h1 style={s.title}>Gallery</h1>
          <div style={s.pageHeaderRight}>
            {!loading && totalPosts > 0 && (
              <span style={s.count}>
                {(groupBy === "vibe" || groupBy === "tags") && selectedKeywords.size > 0
                  ? `${modalPosts.length} / ${totalPosts}`
                  : totalPosts}{" "}
                saved
              </span>
            )}
            {(["date", "keyword", "vibe", "tags"] as GroupBy[]).map((g) => (
              <button
                key={g}
                style={{ ...s.groupBtn, ...(groupBy === g ? s.groupBtnActive : {}) }}
                onClick={() => {
                  setGroupBy(g);
                  setModalIdx(null);
                  setSelectedKeywords(new Set());
                }}
              >
                {g === "date" ? "Date" : g === "keyword" ? "Keyword" : g === "vibe" ? "Vibe" : "Tags"}
              </button>
            ))}
          </div>
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

        {/* Vibe mode: keyword filter chips + flat grid */}
        {groupBy === "vibe" && !loading && allPosts.length > 0 && (
          <>
            {allVibeKeywords.length > 0 ? (
              <div style={s.filterBar}>
                {selectedKeywords.size > 0 && (
                  <button
                    style={s.clearBtn}
                    onClick={() => { setSelectedKeywords(new Set()); setModalIdx(null); }}
                  >
                    Clear
                  </button>
                )}
                {allVibeKeywords.map((kw) => {
                  const active = selectedKeywords.has(kw);
                  return (
                    <button
                      key={kw}
                      style={{ ...s.chip, ...(active ? s.chipActive : {}) }}
                      onClick={() => toggleKeyword(kw)}
                    >
                      {kw}
                      <span style={active ? s.chipCountActive : s.chipCount}>
                        {keywordFreq.get(kw)}
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : (
              <p style={s.empty}>No vibe keywords yet — like some posts to build your taste profile.</p>
            )}
            {vibePosts.length === 0 && selectedKeywords.size > 0 && (
              <p style={s.empty}>No posts match the selected keywords.</p>
            )}
            {vibePosts.length > 0 && (
              <div style={s.grid}>
                {vibePosts.map((post) => (
                  <PostThumb key={post.id} post={post} onClick={() => openModal(post)} />
                ))}
              </div>
            )}
          </>
        )}

        {/* Tags mode: scraped hashtag filter chips + flat grid */}
        {groupBy === "tags" && !loading && allPosts.length > 0 && (
          <>
            {allTags.length > 0 ? (
              <div style={s.filterBar}>
                {selectedKeywords.size > 0 && (
                  <button
                    style={s.clearBtn}
                    onClick={() => { setSelectedKeywords(new Set()); setModalIdx(null); }}
                  >
                    Clear
                  </button>
                )}
                {allTags.map((tag) => {
                  const active = selectedKeywords.has(tag);
                  return (
                    <button
                      key={tag}
                      style={{ ...s.chip, ...(active ? s.chipActive : {}) }}
                      onClick={() => toggleKeyword(tag)}
                    >
                      #{tag}
                      <span style={active ? s.chipCountActive : s.chipCount}>
                        {tagFreq.get(tag)}
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : (
              <p style={s.empty}>No tags yet — tags are scraped from Instagram post hashtags.</p>
            )}
            {tagPosts.length === 0 && selectedKeywords.size > 0 && (
              <p style={s.empty}>No posts match the selected tags.</p>
            )}
            {tagPosts.length > 0 && (
              <div style={s.grid}>
                {tagPosts.map((post) => (
                  <PostThumb key={post.id} post={post} onClick={() => openModal(post)} />
                ))}
              </div>
            )}
          </>
        )}

        {/* Date / Keyword mode: grouped sections */}
        {groupBy !== "vibe" &&
          groups.map((group) => (
            <section key={group.label} style={s.section}>
              <h2 style={s.sectionHeader}>{group.label}</h2>
              <div style={s.grid}>
                {group.posts.map((post) => (
                  <PostThumb key={post.id} post={post} onClick={() => openModal(post)} />
                ))}
              </div>
            </section>
          ))}
      </main>

      {modal && modalIdx !== null && (
        <div style={s.overlay} onClick={() => setModalIdx(null)}>
          <div style={s.modalBox} onClick={(e) => e.stopPropagation()}>
            <button style={s.closeBtn} onClick={() => setModalIdx(null)} title="Close (Esc)">✕</button>

            {modalIdx > 0 && (
              <button style={{ ...s.navBtn, left: "0.5rem" }} onClick={prev} title="Previous (←)">‹</button>
            )}
            {modalIdx < modalPosts.length - 1 && (
              <button style={{ ...s.navBtn, right: "0.5rem" }} onClick={next} title="Next (→)">›</button>
            )}

            {modal.screenshot_url && (
              <div style={s.modalImgWrap}>
                <img src={modal.screenshot_url} alt={`@${modal.creator}`} style={s.modalImg} />
              </div>
            )}
            <div style={s.modalMeta}>
              <PlatformBadge platform={modal.platform} />
              <span style={s.modalCreator}>@{modal.creator}</span>
              <span style={s.modalEngagement}>
                {modal.engagement.toLocaleString()} engagements
              </span>
              <span style={s.modalCounter}>{modalIdx + 1} / {modalPosts.length}</span>
              <button
                style={{ ...s.deleteBtn, ...(deleting ? s.deleteBtnDisabled : {}) }}
                onClick={() => void handleDelete(modal)}
                disabled={deleting}
                title="Delete from gallery"
              >
                {deleting ? "…" : "🗑"}
              </button>
            </div>
            {(modal.vibe_keywords?.length || modal.tags?.length) ? (
              <div style={s.modalKeywords}>
                {modal.vibe_keywords?.map((kw) => (
                  <span key={kw} style={s.modalKwChip}>{kw}</span>
                ))}
                {modal.tags?.map((tag) => (
                  <span key={tag} style={s.modalTagChip}>#{tag}</span>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

function PostThumb({ post, onClick }: { post: GalleryPost; onClick: () => void }) {
  return (
    <button style={s.thumb} onClick={onClick} title={`@${post.creator}`}>
      {post.screenshot_url ? (
        <img src={post.screenshot_url} alt={`@${post.creator}`} style={s.thumbImg} />
      ) : (
        <div style={s.noThumb}>No image</div>
      )}
    </button>
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
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: "2.5rem",
  },
  pageHeaderRight: { display: "flex", alignItems: "center", gap: "0.5rem" },
  title: { margin: 0, fontSize: "1.5rem", fontWeight: 600, letterSpacing: "-0.02em" },
  count: { color: "#555", fontSize: "0.85rem", marginRight: "0.5rem" },
  groupBtn: {
    background: "#1a1a1a",
    border: "1px solid #2a2a2a",
    color: "#666",
    borderRadius: 6,
    padding: "0.3rem 0.7rem",
    fontSize: "0.78rem",
    cursor: "pointer",
    fontWeight: 500,
  },
  groupBtnActive: { background: "#252525", border: "1px solid #444", color: "#e8e8e8" },
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
  filterBar: {
    display: "flex",
    flexWrap: "wrap" as const,
    gap: "0.4rem",
    marginBottom: "1.5rem",
    alignItems: "center",
  },
  clearBtn: {
    background: "transparent",
    border: "1px solid #333",
    color: "#666",
    borderRadius: 999,
    padding: "0.3rem 0.7rem",
    fontSize: "0.75rem",
    cursor: "pointer",
    fontWeight: 500,
  },
  chip: {
    display: "inline-flex",
    alignItems: "center",
    gap: "0.35rem",
    background: "#1a1a1a",
    border: "1px solid #2a2a2a",
    color: "#888",
    borderRadius: 999,
    padding: "0.3rem 0.65rem",
    fontSize: "0.78rem",
    cursor: "pointer",
    fontWeight: 500,
  },
  chipActive: {
    background: "#0d2b1a",
    border: "1px solid #2e7d32",
    color: "#e8e8e8",
  },
  chipCount: { color: "#444", fontSize: "0.7rem" },
  chipCountActive: { color: "#4caf50", fontSize: "0.7rem" },
  section: { marginBottom: "3rem" },
  sectionHeader: {
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
  navBtn: {
    position: "absolute",
    top: "50%",
    transform: "translateY(-50%)",
    background: "rgba(0,0,0,0.6)",
    border: "1px solid #333",
    color: "#ccc",
    borderRadius: "50%",
    width: 44,
    height: 44,
    fontSize: "1.5rem",
    cursor: "pointer",
    zIndex: 2,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    lineHeight: 1,
  },
  modalCounter: { color: "#444", fontSize: "0.75rem", whiteSpace: "nowrap" as const },
  deleteBtn: {
    background: "transparent",
    border: "1px solid #3a1a1a",
    borderRadius: 6,
    color: "#c62828",
    cursor: "pointer",
    fontSize: "1rem",
    padding: "0.15rem 0.5rem",
    marginLeft: "0.5rem",
    flexShrink: 0,
  },
  deleteBtnDisabled: { opacity: 0.4, cursor: "not-allowed" },
  modalKeywords: {
    display: "flex",
    flexWrap: "wrap" as const,
    gap: "0.35rem",
    padding: "0.6rem 1rem",
    borderTop: "1px solid #1e1e1e",
    flexShrink: 0,
  },
  modalKwChip: {
    background: "#111",
    border: "1px solid #2a2a2a",
    color: "#555",
    borderRadius: 999,
    padding: "0.15rem 0.55rem",
    fontSize: "0.72rem",
  },
  modalTagChip: {
    background: "#111",
    border: "1px solid #1e2a1e",
    color: "#3a5a3a",
    borderRadius: 999,
    padding: "0.15rem 0.55rem",
    fontSize: "0.72rem",
  },
};
