/**
 * COP Agona Ahanta — SPA Engine
 * Vanilla ES6+ modules. No framework.
 */

"use strict";

// ── API helper ────────────────────────────────────────────────────────────────
const api = {
  async _fetch(method, path, body) {
    const opts = {
      method,
      headers: { "X-Requested-With": "XMLHttpRequest" },
    };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res  = await fetch(path, opts);
    const json = await res.json().catch(() => ({ ok: false, error: "Parse error" }));
    if (!json.ok) throw new Error(json.error || "Request failed");
    return json;
  },
  get:    (p)    => api._fetch("GET",    p),
  post:   (p, b) => api._fetch("POST",   p, b),
  delete: (p)    => api._fetch("DELETE", p),
};

// ── Toast ─────────────────────────────────────────────────────────────────────
const toast = (() => {
  const el  = document.getElementById("toast");
  let timer = null;
  return (msg, ms = 2800) => {
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(timer);
    timer = setTimeout(() => el.classList.remove("show"), ms);
  };
})();

// ── Theme toggle ──────────────────────────────────────────────────────────────
(() => {
  const root = document.documentElement;
  const btn  = document.getElementById("btn-theme-toggle");
  const sun  = btn.querySelector(".icon-sun");
  const moon = btn.querySelector(".icon-moon");

  const apply = (theme) => {
    root.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
    sun.style.display  = theme === "dark" ? "none" : "";
    moon.style.display = theme === "dark" ? ""     : "none";
  };

  apply(localStorage.getItem("theme") || "light");
  btn.addEventListener("click", () => {
    apply(root.getAttribute("data-theme") === "dark" ? "light" : "dark");
  });
})();

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  me:          null,
  feedCursor:  0,
  feedLoading: false,
  feedDone:    false,
  stories:     [],
  storyIdx:    0,
  storyTimer:  null,
  currentPostId: null,
  commentCursor: 0,
};

// ── Boot ──────────────────────────────────────────────────────────────────────
async function boot() {
  try {
    const { data } = await api.get("/auth/me");
    state.me = data.user;
  } catch {
    location.href = "/auth/login";
    return;
  }

  await Promise.all([
    loadStories(),
    loadFeed(),
    loadNotifCount(),
  ]);

  initNav();
  initNewPost();
  initNotifications();
}

// ── Navigation ────────────────────────────────────────────────────────────────
function initNav() {
  const btns = document.querySelectorAll(".nav-btn[data-view]");
  const views = {
    feed:    document.getElementById("feed"),
    explore: document.getElementById("explore-grid"),
    reels:   document.getElementById("reels-view"),
    give:    document.getElementById("give-view"),
    profile: document.getElementById("profile-view"),
  };
  const stories = document.getElementById("stories-bar");

  btns.forEach((btn) => {
    btn.addEventListener("click", async () => {
      btns.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");

      const view = btn.dataset.view;
      Object.values(views).forEach((v) => v && (v.hidden = true));
      stories.hidden = view !== "feed";

      if (views[view]) views[view].hidden = false;

      if (view === "explore"  && !views.explore.dataset.loaded)  loadExplore();
      if (view === "reels"    && !views.reels.dataset.loaded)    loadReels();
      if (view === "give"     && !views.give.dataset.loaded)     renderGiveView();
      if (view === "profile"  && !views.profile.dataset.loaded)  loadProfile(state.me.username);
    });
  });

  // Infinite scroll on feed
  document.getElementById("app-main").addEventListener("scroll", (e) => {
    const el = e.target;
    if (
      !state.feedLoading &&
      !state.feedDone &&
      el.scrollTop + el.clientHeight >= el.scrollHeight - 200
    ) {
      loadFeed();
    }
  });
}

// ── Stories ───────────────────────────────────────────────────────────────────
async function loadStories() {
  const scroll = document.getElementById("stories-scroll");

  // "Add story" item (own avatar)
  const addItem = buildStoryItem({
    avatar_url:   state.me?.avatar_url || "",
    display_name: "Your Story",
    isAdd:        true,
  });
  scroll.innerHTML = "";
  scroll.appendChild(addItem);

  try {
    const { data } = await api.get("/api/stories");
    state.stories = data;

    if (data.length === 0) return;

    // Group by member
    const byMember = [];
    const seen = new Set();
    data.forEach((s) => {
      if (!seen.has(s.member_id)) {
        seen.add(s.member_id);
        byMember.push(s);
      }
    });

    byMember.forEach((s) => scroll.appendChild(buildStoryItem(s)));
  } catch {
    // Stories are non-critical
  }
}

function buildStoryItem(s) {
  const wrap = document.createElement("div");
  wrap.className = "story-item" + (s.isAdd ? " add-story-btn" : "");

  const ring = document.createElement("div");
  ring.className = "story-ring";

  const img = document.createElement("img");
  img.className = "story-avatar";
  img.src = s.avatar_url || "/static/images/default-avatar.png";
  img.alt = s.display_name;
  ring.appendChild(img);

  const name = document.createElement("span");
  name.className = "story-username";
  name.textContent = s.display_name;

  wrap.appendChild(ring);

  if (s.isAdd) {
    const plus = document.createElement("span");
    plus.className = "plus-icon";
    plus.textContent = "+";
    wrap.appendChild(plus);
    wrap.addEventListener("click", () => document.getElementById("modal-new-post").hidden = false);
  } else {
    wrap.addEventListener("click", () => openStory(s.member_id));
  }

  wrap.appendChild(name);
  return wrap;
}

// ── Story Viewer ──────────────────────────────────────────────────────────────
function openStory(memberId) {
  const stories = state.stories.filter((s) => s.member_id === memberId);
  if (!stories.length) return;

  state.storyIdx = 0;
  showStoryAt(stories, 0);

  const overlay = document.getElementById("modal-story");
  overlay.hidden = false;

  document.getElementById("sv-prev").onclick = () => {
    if (state.storyIdx > 0) showStoryAt(stories, --state.storyIdx);
  };
  document.getElementById("sv-next").onclick = () => {
    if (state.storyIdx < stories.length - 1) showStoryAt(stories, ++state.storyIdx);
    else closeStory();
  };
  document.getElementById("btn-story-close").onclick = closeStory;
  overlay.addEventListener("click", (e) => { if (e.target === overlay) closeStory(); });
}

function showStoryAt(stories, idx) {
  clearTimeout(state.storyTimer);
  const s     = stories[idx];
  const img   = document.getElementById("sv-image");
  const video = document.getElementById("sv-video");
  const bar   = document.getElementById("story-progress-bar");

  document.getElementById("sv-avatar").src    = s.avatar_url || "";
  document.getElementById("sv-username").textContent = s.display_name;
  document.getElementById("sv-time").textContent     = timeAgo(s.created_at);
  document.getElementById("sv-caption").textContent  = s.caption || "";

  // Progress bar
  bar.innerHTML = `<div class="story-progress-fill" id="spf" style="width:0%"></div>`;

  if (s.media_type === "video") {
    img.hidden   = true;
    video.hidden = false;
    video.src    = s.media_url || s.media_key;
    video.play().catch(() => {});
    const duration = 15000;
    animateProgress("spf", duration);
    state.storyTimer = setTimeout(() => {
      if (idx < stories.length - 1) showStoryAt(stories, ++state.storyIdx);
      else closeStory();
    }, duration);
  } else {
    video.hidden = true;
    video.pause();
    img.hidden   = false;
    img.src      = s.media_url || s.media_key;
    const duration = 5000;
    animateProgress("spf", duration);
    state.storyTimer = setTimeout(() => {
      if (idx < stories.length - 1) showStoryAt(stories, ++state.storyIdx);
      else closeStory();
    }, duration);
  }
}

function animateProgress(id, duration) {
  const el = document.getElementById(id);
  if (!el) return;
  let start = null;
  const step = (ts) => {
    if (!start) start = ts;
    const pct = Math.min(((ts - start) / duration) * 100, 100);
    el.style.width = pct + "%";
    if (pct < 100) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function closeStory() {
  clearTimeout(state.storyTimer);
  document.getElementById("modal-story").hidden = true;
  document.getElementById("sv-video").pause();
}

// ── Feed ──────────────────────────────────────────────────────────────────────
async function loadFeed() {
  if (state.feedLoading || state.feedDone) return;
  state.feedLoading = true;

  const feed = document.getElementById("feed");

  // Remove skeletons on first load
  feed.querySelectorAll(".post-skeleton-card").forEach((s) => s.remove());

  // Spinner
  const spinner = document.createElement("div");
  spinner.className = "spinner";
  feed.appendChild(spinner);

  try {
    const res = await api.get(`/api/feed?cursor=${state.feedCursor}`);
    spinner.remove();

    const posts = res.data || [];
    if (!posts.length && state.feedCursor === 0) {
      feed.innerHTML = `<div class="empty-state">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <rect x="3" y="3" width="18" height="18" rx="3"/>
          <path d="M3 9h18M9 21V9"/>
        </svg>
        <p>No posts yet — be the first to share!</p>
      </div>`;
      state.feedDone = true;
      return;
    }

    posts.forEach((p) => feed.appendChild(buildPostCard(p)));

    if (res.next_cursor == null) {
      state.feedDone = true;
    } else {
      state.feedCursor = res.next_cursor;
    }
  } catch (err) {
    spinner.remove();
    toast("Could not load posts. " + err.message);
  } finally {
    state.feedLoading = false;
  }
}

function buildPostCard(post) {
  const card = document.createElement("article");
  card.className = "post-card";
  card.dataset.postId = post.id;

  const likedClass = post.liked ? " action-btn--liked" : "";

  card.innerHTML = `
    <div class="post-header">
      <img class="post-avatar" src="${esc(post.avatar_url) || '/static/images/default-avatar.png'}"
           alt="${esc(post.display_name)}" loading="lazy" />
      <div class="post-meta">
        <div class="post-username">${esc(post.display_name)}</div>
        ${post.location ? `<div class="post-location">${esc(post.location)}</div>` : ""}
      </div>
      <button class="post-more-btn" aria-label="More options">&#8943;</button>
    </div>

    <div class="post-media-wrapper">
      ${post.media_type === "video" || post.media_type === "reel"
        ? `<video src="${esc(post.media_url)}" preload="none" controls playsinline loop></video>`
        : `<img src="${esc(post.media_url)}" alt="Post by ${esc(post.display_name)}" loading="lazy" />`
      }
    </div>

    <div class="post-actions">
      <button class="action-btn action-btn--like${likedClass}" data-action="like" aria-label="Like">
        <svg class="icon" viewBox="0 0 24 24" fill="${post.liked ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2">
          <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
        </svg>
      </button>
      <button class="action-btn" data-action="comment" aria-label="Comment">
        <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
      </button>
      <button class="action-btn save-btn" data-action="save" aria-label="Save">
        <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>
        </svg>
      </button>
    </div>

    <div class="post-info">
      <div class="post-likes" data-likes="${post.like_count}">
        ${post.like_count} ${post.like_count === 1 ? "like" : "likes"}
      </div>
      ${post.caption ? `
        <div class="post-caption">
          <strong>${esc(post.display_name)}</strong>${esc(post.caption)}
        </div>` : ""}
      ${post.comment_count > 0 ? `
        <div class="post-view-comments" data-action="comment">
          View all ${post.comment_count} comments
        </div>` : ""}
      <div class="post-timestamp">${timeAgo(post.created_at)}</div>
    </div>
  `;

  // Like
  card.querySelector("[data-action='like']").addEventListener("click", () => toggleLike(card, post.id));

  // Comments
  card.querySelectorAll("[data-action='comment']").forEach((btn) =>
    btn.addEventListener("click", () => openComments(post.id))
  );

  // Double-tap to like
  let tapTimer = null;
  card.querySelector(".post-media-wrapper").addEventListener("click", () => {
    if (tapTimer) {
      clearTimeout(tapTimer);
      tapTimer = null;
      toggleLike(card, post.id);
    } else {
      tapTimer = setTimeout(() => { tapTimer = null; }, 300);
    }
  });

  return card;
}

async function toggleLike(card, postId) {
  const btn = card.querySelector("[data-action='like']");
  const svg = btn.querySelector("svg");
  try {
    const { data } = await api.post(`/api/posts/${postId}/like`);
    const liked = data.liked;
    btn.classList.toggle("action-btn--liked", liked);
    svg.setAttribute("fill", liked ? "currentColor" : "none");
    btn.classList.add("action-btn--like-anim");
    btn.addEventListener("animationend", () => btn.classList.remove("action-btn--like-anim"), { once: true });
    const likesEl = card.querySelector(".post-likes");
    if (likesEl) {
      const c = data.like_count;
      likesEl.textContent = `${c} ${c === 1 ? "like" : "likes"}`;
    }
  } catch (err) {
    toast("Could not like post. " + err.message);
  }
}

// ── Comments ──────────────────────────────────────────────────────────────────
function openComments(postId) {
  state.currentPostId = postId;
  state.commentCursor = 0;

  const modal = document.getElementById("modal-comments");
  const list  = document.getElementById("comments-list");
  list.innerHTML = `<div class="spinner"></div>`;
  modal.hidden = false;

  const myAvatar = document.getElementById("comment-my-avatar");
  if (state.me) myAvatar.src = state.me.avatar_url || "/static/images/default-avatar.png";

  fetchComments(postId, true);

  modal.querySelectorAll(".modal-close").forEach((b) =>
    b.addEventListener("click", () => { modal.hidden = true; }, { once: true })
  );
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.hidden = true; });

  document.getElementById("btn-post-comment").onclick = postComment;
  document.getElementById("comment-input").onkeydown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); postComment(); }
  };
}

async function fetchComments(postId, reset = false) {
  const list = document.getElementById("comments-list");
  if (reset) list.innerHTML = "";

  try {
    const res = await api.get(`/api/posts/${postId}/comments?cursor=${state.commentCursor}`);
    const comments = res.data || [];

    if (!comments.length && reset) {
      list.innerHTML = `<p style="text-align:center;color:var(--color-text-muted);padding:16px">No comments yet.</p>`;
      return;
    }

    comments.forEach((c) => list.appendChild(buildComment(c)));
    if (res.next_cursor != null) {
      state.commentCursor = res.next_cursor;
      const more = document.createElement("div");
      more.className = "load-more-btn";
      more.textContent = "Load more…";
      more.onclick = () => { more.remove(); fetchComments(postId); };
      list.appendChild(more);
    }
  } catch {
    list.innerHTML = `<p style="text-align:center;color:var(--color-danger);padding:16px">Could not load comments.</p>`;
  }
}

function buildComment(c) {
  const item = document.createElement("div");
  item.className = "comment-item";
  item.innerHTML = `
    <img class="comment-avatar" src="${esc(c.avatar_url) || '/static/images/default-avatar.png'}" alt="${esc(c.display_name)}" />
    <div>
      <div class="comment-body"><strong>${esc(c.display_name)}</strong>${esc(c.body)}</div>
      <div class="comment-time">${timeAgo(c.created_at)}</div>
    </div>
  `;
  return item;
}

async function postComment() {
  const input = document.getElementById("comment-input");
  const text  = input.value.trim();
  if (!text) return;

  const btn = document.getElementById("btn-post-comment");
  btn.disabled = true;
  try {
    const { data } = await api.post(`/api/posts/${state.currentPostId}/comments`, { body: text });
    input.value = "";
    const list = document.getElementById("comments-list");
    list.appendChild(buildComment({
      ...data,
      display_name: state.me.display_name || state.me.username,
      avatar_url:   state.me.avatar_url || "",
    }));
    list.scrollTop = list.scrollHeight;

    // Update comment count in feed card
    const card = document.querySelector(`[data-post-id="${state.currentPostId}"]`);
    if (card) {
      const vc = card.querySelector(".post-view-comments");
      if (vc) {
        const n = parseInt(vc.textContent.match(/\d+/) || 0) + 1;
        vc.textContent = `View all ${n} comments`;
      }
    }
  } catch (err) {
    toast("Could not post comment. " + err.message);
  } finally {
    btn.disabled = false;
  }
}

// ── New Post modal ────────────────────────────────────────────────────────────
function initNewPost() {
  const modal    = document.getElementById("modal-new-post");
  const dropZone = document.getElementById("post-drop-zone");
  const fileInput = document.getElementById("post-file-input");
  const preview  = document.getElementById("post-preview");
  const previewImg = document.getElementById("post-preview-img");
  const previewVid = document.getElementById("post-preview-vid");
  const form     = document.getElementById("post-form");
  const progress = document.getElementById("upload-progress");
  const fill     = document.getElementById("progress-fill");
  const label    = document.getElementById("progress-label");
  const btnSubmit = document.getElementById("btn-submit-post");
  const btnChange = document.getElementById("btn-change-media");

  let uploadedKey = null;
  let selectedFile = null;

  document.getElementById("btn-new-post").addEventListener("click", () => {
    modal.hidden = false;
    resetNewPost();
  });
  modal.querySelectorAll(".modal-close").forEach((b) =>
    b.addEventListener("click", () => { modal.hidden = true; })
  );
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.hidden = true; });

  dropZone.addEventListener("click", () => fileInput.click());
  dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("dragover"); });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    const file = e.dataTransfer.files[0];
    if (file) handleFileSelect(file);
  });
  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) handleFileSelect(fileInput.files[0]);
  });
  btnChange.addEventListener("click", () => { resetNewPost(); fileInput.click(); });

  function handleFileSelect(file) {
    selectedFile = file;
    const isVideo = file.type.startsWith("video/");

    dropZone.hidden  = true;
    preview.hidden   = false;
    form.hidden      = false;

    if (isVideo) {
      previewImg.hidden = true;
      previewVid.hidden = false;
      previewVid.src    = URL.createObjectURL(file);
      document.getElementById("post-type").value = "video";
    } else {
      previewVid.hidden = true;
      previewImg.hidden = false;
      previewImg.src    = URL.createObjectURL(file);
    }
  }

  btnSubmit.addEventListener("click", async () => {
    if (!selectedFile) { toast("Please select a file."); return; }
    btnSubmit.disabled = true;

    try {
      // 1. Get presigned URL
      const presign = await api.post("/api/upload/presign", {
        content_type: selectedFile.type,
        filename:     selectedFile.name,
      });

      // 2. Upload direct to R2
      dropZone.hidden  = true;
      preview.hidden   = true;
      progress.hidden  = false;
      form.hidden      = true;

      await uploadToR2(presign.data.upload_url, selectedFile, (pct) => {
        fill.style.width = pct + "%";
        label.textContent = pct < 100 ? `Uploading… ${pct}%` : "Processing…";
      });

      uploadedKey = presign.data.object_key;

      // 3. Create post record
      await api.post("/api/posts", {
        media_key:  uploadedKey,
        media_type: document.getElementById("post-type").value,
        caption:    document.getElementById("post-caption").value.trim(),
        location:   document.getElementById("post-location").value.trim(),
      });

      modal.hidden = true;
      toast("Posted!");
      // Prepend to feed
      state.feedCursor = 0;
      state.feedDone   = false;
      const feed = document.getElementById("feed");
      feed.innerHTML   = "";
      await loadFeed();
    } catch (err) {
      toast("Upload failed: " + err.message);
      progress.hidden  = true;
      preview.hidden   = false;
      form.hidden      = false;
    } finally {
      btnSubmit.disabled = false;
    }
  });

  function resetNewPost() {
    dropZone.hidden  = false;
    preview.hidden   = true;
    progress.hidden  = true;
    form.hidden      = true;
    previewImg.src   = "";
    previewVid.src   = "";
    fill.style.width = "0%";
    fileInput.value  = "";
    document.getElementById("post-caption").value  = "";
    document.getElementById("post-location").value = "";
    selectedFile = null;
    uploadedKey  = null;
  }
}

async function uploadToR2(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", file.type);
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
    });
    xhr.onload = () => { if (xhr.status < 300) resolve(); else reject(new Error(`R2 ${xhr.status}`)); };
    xhr.onerror = () => reject(new Error("Network error"));
    xhr.send(file);
  });
}

// ── Notifications ─────────────────────────────────────────────────────────────
async function loadNotifCount() {
  try {
    const res = await api.get("/api/notifications");
    updateBadge(res.unread_count);
  } catch {}
}

function updateBadge(count) {
  const badge = document.getElementById("notif-badge");
  badge.hidden = count === 0;
  badge.textContent = count > 99 ? "99+" : String(count);
}

function initNotifications() {
  const btn   = document.getElementById("btn-notifications");
  const panel = document.getElementById("notif-panel");

  btn.addEventListener("click", async () => {
    if (!panel.hidden) { panel.hidden = true; return; }
    panel.hidden = false;
    panel.innerHTML = `<div class="spinner"></div>`;

    try {
      const res = await api.get("/api/notifications");
      panel.innerHTML = "";

      if (!res.data.length) {
        panel.innerHTML = `<p style="padding:16px;color:var(--color-text-muted);text-align:center">No notifications.</p>`;
        return;
      }

      res.data.forEach((n) => {
        const item = document.createElement("div");
        item.className = "notif-item" + (n.is_read ? "" : " unread");
        item.innerHTML = `
          <img class="notif-avatar" src="${esc(n.actor_avatar_url) || '/static/images/default-avatar.png'}" />
          <span class="notif-text">
            <strong>${esc(n.actor_name || "Someone")}</strong>
            ${notifText(n.type)}
          </span>
          <span class="notif-time">${timeAgo(n.created_at)}</span>
        `;
        panel.appendChild(item);
      });

      // Mark all read
      await api.post("/api/notifications/read");
      updateBadge(0);
    } catch {
      panel.innerHTML = `<p style="padding:16px;color:var(--color-danger)">Could not load.</p>`;
    }
  });

  document.addEventListener("click", (e) => {
    if (!panel.contains(e.target) && e.target !== btn) panel.hidden = true;
  });
}

function notifText(type) {
  const map = {
    like:    " liked your post.",
    comment: " commented on your post.",
    follow:  " started following you.",
    mention: " mentioned you.",
  };
  return map[type] || " interacted with you.";
}

// ── Explore ───────────────────────────────────────────────────────────────────
async function loadExplore() {
  const grid = document.getElementById("explore-grid");
  grid.dataset.loaded = "1";
  grid.innerHTML = `<div class="spinner" style="grid-column:1/-1"></div>`;

  try {
    const res = await api.get("/api/search?q=");
    grid.innerHTML = "";

    if (!res.data.length) {
      grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1">No members found.</div>`;
      return;
    }

    res.data.forEach((u) => {
      const thumb = document.createElement("div");
      thumb.className = "explore-thumb";
      thumb.innerHTML = `<img src="${esc(u.avatar_url) || '/static/images/default-avatar.png'}" alt="${esc(u.display_name)}" loading="lazy" />`;
      thumb.addEventListener("click", () => {
        switchToProfile(u.username);
      });
      grid.appendChild(thumb);
    });
  } catch {
    grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1">Could not load explore.</div>`;
  }
}

// ── Reels ─────────────────────────────────────────────────────────────────────
async function loadReels() {
  const view = document.getElementById("reels-view");
  view.dataset.loaded = "1";
  view.innerHTML = `<div class="spinner"></div>`;

  try {
    const res = await api.get("/api/feed?cursor=0");
    view.innerHTML = "";

    const reels = (res.data || []).filter((p) => p.media_type === "video" || p.media_type === "reel");
    if (!reels.length) {
      view.innerHTML = `<div class="empty-state"><p>No video sermons yet.</p></div>`;
      return;
    }

    reels.forEach((r) => {
      const card = document.createElement("div");
      card.className = "reel-card";
      card.innerHTML = `
        <video src="${esc(r.media_url)}" preload="none" loop playsinline></video>
        <div class="reel-overlay">
          <div class="post-caption"><strong>${esc(r.display_name)}</strong>${esc(r.caption)}</div>
          <div class="post-timestamp">${timeAgo(r.created_at)}</div>
        </div>
        <div class="reel-actions">
          <button class="action-btn" data-action="like" aria-label="Like">
            <svg class="icon" style="stroke:#fff" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
            </svg>
            <span style="color:#fff;font-size:12px">${r.like_count}</span>
          </button>
        </div>
      `;
      // Intersection Observer to auto-play
      const video = card.querySelector("video");
      const obs = new IntersectionObserver((entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) video.play().catch(() => {});
          else { video.pause(); video.currentTime = 0; }
        });
      }, { threshold: 0.7 });
      obs.observe(card);

      view.appendChild(card);
    });
  } catch {
    view.innerHTML = `<div class="empty-state"><p>Could not load sermons.</p></div>`;
  }
}

// ── Profile ───────────────────────────────────────────────────────────────────
async function loadProfile(username) {
  const view = document.getElementById("profile-view");
  view.dataset.loaded = "1";
  view.hidden = false;
  view.innerHTML = `<div class="spinner"></div>`;

  try {
    const { data } = await api.get(`/api/profile/${encodeURIComponent(username)}`);
    renderProfile(data);
  } catch (err) {
    view.innerHTML = `<div class="empty-state"><p>Could not load profile.</p></div>`;
  }
}

function renderProfile(data) {
  const view = document.getElementById("profile-view");

  view.innerHTML = `
    <div class="profile-header">
      <img class="profile-avatar-lg" src="${esc(data.avatar_url) || '/static/images/default-avatar.png'}" alt="${esc(data.display_name)}" />
      <div>
        <div style="font-weight:700;font-size:16px">${esc(data.display_name)}</div>
        <div class="profile-stats">
          <div class="stat-item">
            <div class="stat-item-val">${data.post_count}</div>
            <div class="stat-item-lbl">Posts</div>
          </div>
          <div class="stat-item">
            <div class="stat-item-val">${data.follower_count}</div>
            <div class="stat-item-lbl">Followers</div>
          </div>
          <div class="stat-item">
            <div class="stat-item-val">${data.following_count}</div>
            <div class="stat-item-lbl">Following</div>
          </div>
        </div>
        ${data.is_own
          ? `<button class="btn btn-ghost btn-sm" style="margin-top:8px" id="btn-edit-profile">Edit Profile</button>`
          : `<button class="btn ${data.is_following ? 'btn-ghost' : 'btn-primary'} btn-sm" style="margin-top:8px"
               id="btn-follow-toggle">${data.is_following ? "Following" : "Follow"}</button>`
        }
      </div>
    </div>
    ${data.bio ? `<div class="profile-bio">${esc(data.bio)}</div>` : ""}
    <div class="profile-grid" id="profile-post-grid"></div>
  `;

  const grid = view.querySelector("#profile-post-grid");
  data.posts.forEach((p) => {
    const thumb = document.createElement("div");
    thumb.className = "explore-thumb";
    thumb.innerHTML = `<img src="${esc(p.media_url)}" alt="Post" loading="lazy" />`;
    thumb.addEventListener("click", () => openComments(p.id));
    grid.appendChild(thumb);
  });

  if (!data.is_own) {
    const followBtn = view.querySelector("#btn-follow-toggle");
    if (followBtn) {
      followBtn.addEventListener("click", async () => {
        try {
          const res = await api.post(`/api/profile/${encodeURIComponent(data.username)}/follow`);
          followBtn.textContent = res.data.following ? "Following" : "Follow";
          followBtn.className   = `btn ${res.data.following ? 'btn-ghost' : 'btn-primary'} btn-sm`;
        } catch (err) {
          toast(err.message);
        }
      });
    }
  }
}

function switchToProfile(username) {
  document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
  document.querySelectorAll(".app-main > section").forEach((s) => { s.hidden = true; });
  loadProfile(username);
}

// ── Give View ─────────────────────────────────────────────────────────────────
function renderGiveView() {
  const view = document.getElementById("give-view");
  view.dataset.loaded = "1";

  const presets = [10, 20, 50, 100, 200, 500];

  view.innerHTML = `
    <h2>Give / Tithe</h2>
    <div class="give-card">
      <div class="give-amount-grid">
        ${presets.map((a) => `<button class="give-preset" data-amount="${a}">GHS ${a}</button>`).join("")}
      </div>
      <div class="form-group">
        <input type="number" id="give-custom" class="form-input" placeholder="Custom amount (GHS)" min="1" step="0.01" />
      </div>
      <div class="form-group">
        <select id="give-category" class="form-input">
          <option value="tithe">Tithe</option>
          <option value="offering">Offering</option>
          <option value="pledge">Pledge</option>
          <option value="special">Special Offering</option>
        </select>
      </div>
      <button class="btn btn-primary" id="btn-give">Give Now</button>
      <div id="give-result" style="display:none;text-align:center;padding:12px;"></div>
    </div>
  `;

  let selectedAmount = null;

  view.querySelectorAll(".give-preset").forEach((btn) => {
    btn.addEventListener("click", () => {
      view.querySelectorAll(".give-preset").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
      selectedAmount = parseFloat(btn.dataset.amount);
      document.getElementById("give-custom").value = "";
    });
  });

  document.getElementById("btn-give").addEventListener("click", async () => {
    const custom = parseFloat(document.getElementById("give-custom").value);
    const amount = custom > 0 ? custom : selectedAmount;
    if (!amount || amount <= 0) { toast("Please select or enter an amount."); return; }

    const category = document.getElementById("give-category").value;
    const btn = document.getElementById("btn-give");
    btn.disabled = true;

    try {
      const { data } = await api.post("/api/give", { amount, category });
      const result = document.getElementById("give-result");
      result.style.display = "block";
      result.innerHTML = `
        <svg viewBox="0 0 24 24" fill="none" stroke="var(--color-success)" stroke-width="2" width="40" style="margin:0 auto 8px">
          <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
          <polyline points="22 4 12 14.01 9 11.01"/>
        </svg>
        <p style="color:var(--color-success);font-weight:700">Thank you for your offering!</p>
        <p style="font-size:12px;color:var(--color-text-muted)">Reference: ${esc(data.reference)}</p>
      `;
      toast("Giving recorded! Reference: " + data.reference);
    } catch (err) {
      toast("Giving failed: " + err.message);
    } finally {
      btn.disabled = false;
    }
  });
}

// ── Utilities ─────────────────────────────────────────────────────────────────
function esc(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function timeAgo(iso) {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso + (iso.endsWith("Z") ? "" : "Z")).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60)     return `${s}s`;
  if (s < 3600)   return `${Math.floor(s / 60)}m`;
  if (s < 86400)  return `${Math.floor(s / 3600)}h`;
  if (s < 604800) return `${Math.floor(s / 86400)}d`;
  return new Date(iso).toLocaleDateString();
}

// ── Start ─────────────────────────────────────────────────────────────────────
boot();
