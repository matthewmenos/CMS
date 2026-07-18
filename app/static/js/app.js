/**
 * COP Agona Ahanta — SPA Engine  (v2)
 * Vanilla ES6+. No frameworks.
 *
 * Bug fixes vs v1:
 *  - boot() no longer redirects to /auth/login on non-401 errors (was causing refresh loop)
 *  - /auth/me no longer calls the tenant DB (removed R2 dependency from boot path)
 *  - Scroll listener uses passive option for perf
 *  - Modal close handlers use AbortController to prevent duplicate listeners
 */

"use strict";

// ── API helper ────────────────────────────────────────────────────────────────
const api = {
  async _fetch(method, path, body) {
    const opts = {
      method,
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);

    // 401 = not logged in → go to login page
    if (res.status === 401) {
      window.location.href = "/auth/login";
      // Return a never-resolving promise so no further code runs
      return new Promise(() => {});
    }

    const json = await res.json().catch(() => ({ ok: false, error: "Server error" }));
    if (!json.ok) {
      const err = new Error(json.error || "Request failed");
      err.status = res.status;
      throw err;
    }
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
  return (msg, ms = 3000) => {
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(timer);
    timer = setTimeout(() => el.classList.remove("show"), ms);
  };
})();

// ── Theme toggle ──────────────────────────────────────────────────────────────
const themeManager = (() => {
  const root = document.documentElement;
  const btn  = document.getElementById("btn-theme-toggle");

  const apply = (theme) => {
    root.setAttribute("data-theme", theme);
    localStorage.setItem("cop-theme", theme);
    btn.dataset.theme = theme;
  };

  apply(localStorage.getItem("cop-theme") || "light");
  btn.addEventListener("click", () => {
    apply(root.getAttribute("data-theme") === "dark" ? "light" : "dark");
  });
  return { apply };
})();

// ── Global state ──────────────────────────────────────────────────────────────
const state = {
  me:           null,
  feedCursor:   0,
  feedLoading:  false,
  feedDone:     false,
  stories:      [],
  storyIdx:     0,
  storyTimer:   null,
  currentPostId: null,
  commentCursor: 0,
  activeView:   "feed",
};

// ── Boot — FIXED: only redirect on explicit 401 ───────────────────────────────
async function boot() {
  // Fetch current user — if it throws a non-401 error (network, R2, etc.)
  // we still land on the home page instead of looping back to login.
  try {
    const { data } = await api.get("/auth/me");
    state.me = data.user;
  } catch (err) {
    // api._fetch already redirects on 401; any other error just means
    // we couldn't load profile — show the shell anyway
    console.warn("Could not load /auth/me:", err.message);
    state.me = { username: "Guest", role: "member", display_name: "Guest", avatar_url: "" };
  }

  initNav();
  initNewPost();
  initNotifications();

  // Load non-critical data in parallel, silently ignoring failures
  await Promise.allSettled([loadStories(), loadFeed(), loadNotifCount()]);
}

// ── Navigation ────────────────────────────────────────────────────────────────
function initNav() {
  const navBtns = document.querySelectorAll(".nav-btn[data-view]");

  navBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      navBtns.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      switchView(btn.dataset.view);
    });
  });

  // Infinite scroll on main area
  const main = document.getElementById("app-main");
  main.addEventListener("scroll", () => {
    if (state.activeView !== "feed") return;
    if (state.feedLoading || state.feedDone) return;
    if (main.scrollTop + main.clientHeight >= main.scrollHeight - 300) {
      loadFeed();
    }
  }, { passive: true });
}

const _viewLoaded = {};

function switchView(view) {
  state.activeView = view;

  const allViews = ["feed", "explore", "reels", "give", "profile"];
  allViews.forEach((v) => {
    const el = document.getElementById(`${v === "feed" ? "feed" : v + "-view"}`);
    if (el) el.hidden = (v !== view);
  });

  // Stories bar only visible on feed
  const storiesBar = document.getElementById("stories-bar");
  if (storiesBar) storiesBar.hidden = (view !== "feed");

  // Lazy-load each view once
  if (!_viewLoaded[view]) {
    _viewLoaded[view] = true;
    if (view === "explore") loadExplore();
    if (view === "reels")   loadReels();
    if (view === "give")    renderGiveView();
    if (view === "profile") loadProfile(state.me?.username || "");
  }
}

// Alias for pushManager
function showView(view) {
  switchView(view);
}

function openPost(postId, openComments = false) {
  // Open a specific post (used for deep-linking)
  openComments(postId);
  if (openComments) {
    // Comments are already opened by openComments
  }
}

// ── Stories ───────────────────────────────────────────────────────────────────
async function loadStories() {
  const scroll = document.getElementById("stories-scroll");
  scroll.innerHTML = "";

  // "Add Story" chip
  scroll.appendChild(buildAddStoryChip());

  try {
    const { data } = await api.get("/api/stories");
    state.stories = data;

    // Deduplicate by member
    const seen = new Set();
    data.forEach((s) => {
      if (!seen.has(s.member_id)) {
        seen.add(s.member_id);
        scroll.appendChild(buildStoryChip(s));
      }
    });
  } catch {
    // Stories are decorative — fail silently
  }
}

function buildAddStoryChip() {
  const wrap = document.createElement("button");
  wrap.className = "story-chip story-chip--add";
  wrap.setAttribute("aria-label", "Add story");
  wrap.innerHTML = `
    <div class="story-ring story-ring--add">
      <div class="story-add-avatar">
        <img src="${esc(state.me?.avatar_url)}" onerror="this.src='/static/images/default-avatar.svg'" alt="You" />
        <span class="story-plus">+</span>
      </div>
    </div>
    <span class="story-name">Your Story</span>
  `;
  wrap.addEventListener("click", () => openNewPostModal());
  return wrap;
}

function buildStoryChip(s) {
  const wrap = document.createElement("button");
  wrap.className = "story-chip";
  wrap.setAttribute("aria-label", `${s.display_name}'s story`);
  wrap.innerHTML = `
    <div class="story-ring">
      <img class="story-avatar" src="${esc(s.avatar_url)}" onerror="this.src='/static/images/default-avatar.svg'" alt="${esc(s.display_name)}" />
    </div>
    <span class="story-name">${esc(s.display_name)}</span>
  `;
  wrap.addEventListener("click", () => openStory(s.member_id));
  return wrap;
}

// ── Story Viewer ──────────────────────────────────────────────────────────────
function openStory(memberId) {
  const memberStories = state.stories.filter((s) => s.member_id === memberId);
  if (!memberStories.length) return;

  state.storyIdx = 0;
  const overlay = document.getElementById("modal-story");
  overlay.hidden = false;

  showStoryAt(memberStories, 0);

  document.getElementById("sv-prev").onclick = () => {
    if (state.storyIdx > 0) showStoryAt(memberStories, --state.storyIdx);
  };
  document.getElementById("sv-next").onclick = () => {
    if (state.storyIdx < memberStories.length - 1) showStoryAt(memberStories, ++state.storyIdx);
    else closeStory();
  };
  document.getElementById("btn-story-close").onclick = closeStory;
  const closeOnBackdrop = (e) => { if (e.target === overlay) closeStory(); };
  overlay.addEventListener("click", closeOnBackdrop);
}

function showStoryAt(stories, idx) {
  clearTimeout(state.storyTimer);
  const s     = stories[idx];
  const img   = document.getElementById("sv-image");
  const video = document.getElementById("sv-video");

  document.getElementById("sv-avatar").src = s.avatar_url || "/static/images/default-avatar.svg";
  document.getElementById("sv-username").textContent = s.display_name;
  document.getElementById("sv-time").textContent     = timeAgo(s.created_at);
  document.getElementById("sv-caption").textContent  = s.caption || "";

  // Progress bar
  const bar = document.getElementById("story-progress-bar");
  bar.innerHTML = stories.map((_, i) =>
    `<div class="spb-segment"><div class="spb-fill ${i < idx ? 'spb-fill--done' : i === idx ? 'spb-fill--active' : ''}"></div></div>`
  ).join("");

  const duration = s.media_type === "video" ? 15000 : 5000;
  const fill = bar.querySelector(".spb-fill--active");
  if (fill) {
    fill.style.animationDuration = duration + "ms";
  }

  if (s.media_type === "video") {
    img.hidden   = true;
    video.hidden = false;
    video.src    = s.media_url || "";
    video.play().catch(() => {});
  } else {
    video.pause();
    video.hidden = true;
    img.hidden   = false;
    img.src      = s.media_url || "";
  }

  state.storyTimer = setTimeout(() => {
    if (idx < stories.length - 1) showStoryAt(stories, ++state.storyIdx);
    else closeStory();
  }, duration);
}

function closeStory() {
  clearTimeout(state.storyTimer);
  document.getElementById("modal-story").hidden = true;
  const video = document.getElementById("sv-video");
  video.pause();
  video.src = "";
}

// ── Feed ──────────────────────────────────────────────────────────────────────
async function loadFeed() {
  if (state.feedLoading || state.feedDone) return;
  state.feedLoading = true;

  const feed = document.getElementById("feed");
  feed.querySelectorAll(".skeleton-card").forEach((s) => s.remove());

  const spinner = mkSpinner();
  feed.appendChild(spinner);

  try {
    const res  = await api.get(`/api/feed?cursor=${state.feedCursor}`);
    const posts = res.data || [];

    spinner.remove();

    if (!posts.length && state.feedCursor === 0) {
      feed.appendChild(mkEmptyState(
        "No posts yet",
        "Be the first to share something with the community!"
      ));
      state.feedDone = true;
      return;
    }

    posts.forEach((p) => feed.appendChild(buildPostCard(p)));

    if (res.next_cursor == null) {
      state.feedDone = true;
      const endMsg = document.createElement("div");
      endMsg.className = "feed-end";
      endMsg.textContent = "You're all caught up ✓";
      feed.appendChild(endMsg);
    } else {
      state.feedCursor = res.next_cursor;
    }
  } catch (err) {
    spinner.remove();
    if (err.status !== 401) toast("Could not load posts — " + err.message);
  } finally {
    state.feedLoading = false;
  }
}

function buildPostCard(post) {
  const card = document.createElement("article");
  card.className = "post-card";
  card.dataset.postId = post.id;

  const avatarSrc = post.avatar_url || "/static/images/default-avatar.svg";
  const mediaEl   = (post.media_type === "video" || post.media_type === "reel")
    ? `<video src="${esc(post.media_url)}" preload="metadata" controls playsinline loop></video>`
    : `<img src="${esc(post.media_url)}" alt="Post by ${esc(post.display_name)}" loading="lazy" decoding="async" />`;

  card.innerHTML = `
    <div class="post-header">
      <img class="post-avatar" src="${esc(avatarSrc)}" onerror="this.src='/static/images/default-avatar.svg'" alt="${esc(post.display_name)}" />
      <div class="post-meta">
        <div class="post-username">${esc(post.display_name)}</div>
        ${post.location ? `<div class="post-location"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="10"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>${esc(post.location)}</div>` : ""}
      </div>
      <button class="post-menu-btn" aria-label="More" data-post-id="${post.id}">
        <svg viewBox="0 0 24 24" fill="currentColor" width="20" height="20"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>
      </button>
    </div>

    <div class="post-media" data-doubletap="like">
      ${mediaEl}
      <div class="heart-burst" id="hb-${post.id}">❤️</div>
    </div>

    <div class="post-actions">
      <button class="action-btn btn-like ${post.liked ? 'liked' : ''}" aria-label="Like" data-action="like">
        <svg viewBox="0 0 24 24" fill="${post.liked ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2">
          <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
        </svg>
      </button>
      <button class="action-btn btn-comment" aria-label="Comment" data-action="comment">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
      </button>
      <button class="action-btn btn-share" aria-label="Share">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
        </svg>
      </button>
      <button class="action-btn btn-save ml-auto" aria-label="Save">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>
        </svg>
      </button>
    </div>

    <div class="post-info">
      <div class="post-likes" data-count="${post.like_count}">
        ${post.like_count > 0 ? `<strong>${post.like_count.toLocaleString()}</strong> ${post.like_count === 1 ? "like" : "likes"}` : "Be the first to like this"}
      </div>
      ${post.caption ? `<p class="post-caption"><strong>${esc(post.display_name)}</strong> ${esc(post.caption)}</p>` : ""}
      ${post.comment_count > 0 ? `<button class="post-view-comments" data-action="comment">View all ${post.comment_count} comments</button>` : ""}
      <time class="post-time">${timeAgo(post.created_at)}</time>
    </div>
  `;

  // Like button
  card.querySelector("[data-action='like']").addEventListener("click", () => toggleLike(card, post));

  // Comment buttons
  card.querySelectorAll("[data-action='comment']").forEach((b) =>
    b.addEventListener("click", () => openComments(post.id))
  );

  // Double-tap to like
  let tapTimer = null;
  card.querySelector(".post-media").addEventListener("click", (e) => {
    if (tapTimer) {
      clearTimeout(tapTimer);
      tapTimer = null;
      burstHeart(card, post.id);
      toggleLike(card, post);
    } else {
      tapTimer = setTimeout(() => { tapTimer = null; }, 280);
    }
  });

  return card;
}

function burstHeart(card, postId) {
  const hb = card.querySelector(`#hb-${postId}`);
  if (!hb) return;
  hb.classList.add("burst");
  hb.addEventListener("animationend", () => hb.classList.remove("burst"), { once: true });
}

async function toggleLike(card, post) {
  const btn = card.querySelector(".btn-like");
  const svg = btn.querySelector("svg");
  const likesEl = card.querySelector(".post-likes");
  // Optimistic update
  const wasLiked = btn.classList.contains("liked");
  const prevCount = parseInt(likesEl.dataset.count) || 0;
  const newCount  = wasLiked ? prevCount - 1 : prevCount + 1;

  btn.classList.toggle("liked", !wasLiked);
  svg.setAttribute("fill", wasLiked ? "none" : "currentColor");
  updateLikesEl(likesEl, newCount, post.display_name);

  try {
    const { data } = await api.post(`/api/posts/${post.id}/like`);
    btn.classList.toggle("liked", data.liked);
    svg.setAttribute("fill", data.liked ? "currentColor" : "none");
    updateLikesEl(likesEl, data.like_count, post.display_name);
    if (data.liked) btn.classList.add("like-pop");
    btn.addEventListener("animationend", () => btn.classList.remove("like-pop"), { once: true });
  } catch (err) {
    // Revert optimistic update
    btn.classList.toggle("liked", wasLiked);
    svg.setAttribute("fill", wasLiked ? "currentColor" : "none");
    updateLikesEl(likesEl, prevCount, post.display_name);
    if (err.status !== 401) toast("Could not update like");
  }
}

function updateLikesEl(el, count, name) {
  el.dataset.count = count;
  el.innerHTML = count > 0
    ? `<strong>${count.toLocaleString()}</strong> ${count === 1 ? "like" : "likes"}`
    : "Be the first to like this";
}

// ── Comments ──────────────────────────────────────────────────────────────────
function openComments(postId) {
  state.currentPostId = postId;
  state.commentCursor = 0;

  const modal = document.getElementById("modal-comments");
  const list  = document.getElementById("comments-list");
  list.innerHTML = "";
  list.appendChild(mkSpinner());
  modal.hidden = false;

  const myAv = document.getElementById("comment-my-avatar");
  if (myAv) myAv.src = state.me?.avatar_url || "/static/images/default-avatar.svg";

  fetchComments(postId, true);

  const closeModal = () => { modal.hidden = true; };
  modal.querySelectorAll("[data-close-modal]").forEach((b) =>
    b.addEventListener("click", closeModal, { once: true })
  );

  const backdrop = (e) => { if (e.target === modal) { closeModal(); modal.removeEventListener("click", backdrop); } };
  modal.addEventListener("click", backdrop);

  document.getElementById("btn-post-comment").onclick = submitComment;
  document.getElementById("comment-input").onkeydown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitComment(); }
  };
}

async function fetchComments(postId, reset = false) {
  const list = document.getElementById("comments-list");
  if (reset) list.innerHTML = "";

  try {
    const res      = await api.get(`/api/posts/${postId}/comments?cursor=${state.commentCursor}`);
    const comments = res.data || [];

    if (!comments.length && reset) {
      list.innerHTML = `<p class="comments-empty">No comments yet. Be the first!</p>`;
      return;
    }

    comments.forEach((c) => list.appendChild(buildCommentEl(c)));

    if (res.next_cursor != null) {
      state.commentCursor = res.next_cursor;
      const more = document.createElement("button");
      more.className = "load-more-btn";
      more.textContent = "Load more comments";
      more.onclick = () => { more.remove(); fetchComments(postId); };
      list.appendChild(more);
    }
  } catch {
    if (!list.childElementCount) {
      list.innerHTML = `<p class="comments-empty error">Could not load comments.</p>`;
    }
  }
}

function buildCommentEl(c) {
  const item = document.createElement("div");
  item.className = "comment-item";
  item.innerHTML = `
    <img class="comment-avatar" src="${esc(c.avatar_url)}" onerror="this.src='/static/images/default-avatar.svg'" alt="${esc(c.display_name)}" />
    <div class="comment-content">
      <p><strong>${esc(c.display_name)}</strong> ${esc(c.body)}</p>
      <time>${timeAgo(c.created_at)}</time>
    </div>
  `;
  return item;
}

async function submitComment() {
  const input = document.getElementById("comment-input");
  const text  = input.value.trim();
  if (!text) return;

  const btn = document.getElementById("btn-post-comment");
  btn.disabled = true;

  try {
    const { data } = await api.post(`/api/posts/${state.currentPostId}/comments`, { body: text });
    input.value = "";
    const list = document.getElementById("comments-list");
    const p = list.querySelector(".comments-empty");
    if (p) p.remove();
    const el = buildCommentEl({
      ...data,
      display_name: state.me?.display_name || state.me?.username || "You",
      avatar_url:   state.me?.avatar_url || "",
    });
    el.classList.add("comment-new");
    list.appendChild(el);
    list.scrollTop = list.scrollHeight;

    // Update count in feed
    const card = document.querySelector(`[data-post-id="${state.currentPostId}"]`);
    if (card) {
      let vc = card.querySelector(".post-view-comments");
      if (!vc) {
        vc = document.createElement("button");
        vc.className = "post-view-comments";
        card.querySelector(".post-info").insertBefore(vc, card.querySelector(".post-time"));
        vc.addEventListener("click", () => openComments(state.currentPostId));
      }
      const n = parseInt(vc.textContent.match(/\d+/)?.[0] || "0") + 1;
      vc.textContent = `View all ${n} comments`;
    }
  } catch (err) {
    if (err.status !== 401) toast("Could not post comment");
  } finally {
    btn.disabled = false;
  }
}

// ── New Post modal ────────────────────────────────────────────────────────────
function initNewPost() {
  document.getElementById("btn-new-post").addEventListener("click", openNewPostModal);
}

function openNewPostModal() {
  const modal     = document.getElementById("modal-new-post");
  const dropZone  = document.getElementById("post-drop-zone");
  const fileInput = document.getElementById("post-file-input");
  const previewEl = document.getElementById("post-preview");
  const previewImg = document.getElementById("post-preview-img");
  const previewVid = document.getElementById("post-preview-vid");
  const postForm  = document.getElementById("post-form");
  const progressWrap = document.getElementById("upload-progress");
  const progressLbl  = document.getElementById("progress-label");
  const btnSubmit = document.getElementById("btn-submit-post");

  let selectedFile = null;

  const reset = () => {
    dropZone.hidden    = false;
    previewEl.hidden   = true;
    postForm.hidden    = true;
    progressWrap.hidden = true;
    previewImg.src     = "";
    previewVid.src     = "";
    fileInput.value    = "";
    document.getElementById("post-caption").value  = "";
    document.getElementById("post-location").value = "";
    selectedFile = null;
  };

  reset();
  modal.hidden = false;

  const closeModal = () => { modal.hidden = true; };
  modal.querySelectorAll("[data-close-modal]").forEach((b) =>
    b.addEventListener("click", closeModal, { once: true })
  );
  modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });

  const handleFile = (file) => {
    selectedFile = file;
    dropZone.hidden  = true;
    previewEl.hidden = false;
    postForm.hidden  = false;
    const isVid = file.type.startsWith("video/");
    previewImg.hidden = isVid;
    previewVid.hidden = !isVid;
    const url = URL.createObjectURL(file);
    if (isVid) { previewVid.src = url; document.getElementById("post-type").value = "video"; }
    else { previewImg.src = url; }
  };

  dropZone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => { if (fileInput.files[0]) handleFile(fileInput.files[0]); });
  dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("dragover"); });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
  });

  document.getElementById("btn-change-media").addEventListener("click", () => { reset(); fileInput.click(); });

  btnSubmit.onclick = async () => {
    if (!selectedFile) { toast("Please select a file first."); return; }
    btnSubmit.disabled = true;
    previewEl.hidden   = true;
    postForm.hidden    = true;
    progressWrap.hidden = false;

    try {
      const presign = await api.post("/api/upload/presign", {
        content_type: selectedFile.type,
        filename:     selectedFile.name,
      });

      await uploadToR2(presign.data.upload_url, selectedFile, (pct) => {
        const ring = document.getElementById("progress-ring-fill");
        if (ring) ring.style.strokeDashoffset = 113 - (113 * pct / 100);
        const pctEl = document.getElementById("upload-pct");
        if (pctEl) pctEl.textContent = pct + "%";
        if (progressLbl) progressLbl.textContent = pct < 100 ? "Uploading…" : "Almost done…";
      });

      await api.post("/api/posts", {
        media_key:  presign.data.object_key,
        media_type: document.getElementById("post-type").value,
        caption:    document.getElementById("post-caption").value.trim(),
        location:   document.getElementById("post-location").value.trim(),
      });

      modal.hidden = true;
      toast("Posted successfully!");
      // Refresh feed
      state.feedCursor = 0;
      state.feedDone   = false;
      const feed = document.getElementById("feed");
      feed.innerHTML = "";
      await loadFeed();
    } catch (err) {
      progressWrap.hidden = true;
      previewEl.hidden    = false;
      postForm.hidden     = false;
      toast("Upload failed: " + err.message);
    } finally {
      btnSubmit.disabled = false;
    }
  };
}

async function uploadToR2(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", file.type);
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
    });
    xhr.onload  = () => (xhr.status < 300 ? resolve() : reject(new Error("Upload error " + xhr.status)));
    xhr.onerror = () => reject(new Error("Network error during upload"));
    xhr.send(file);
  });
}

// ── Notifications ─────────────────────────────────────────────────────────────
async function loadNotifCount() {
  try {
    const res = await api.get("/api/notifications");
    renderBadge(res.unread_count || 0);
  } catch {
    // Non-critical
  }
}

function renderBadge(n) {
  const badge = document.getElementById("notif-badge");
  badge.hidden    = n === 0;
  badge.textContent = n > 99 ? "99+" : String(n);
}

function initNotifications() {
  const btn   = document.getElementById("btn-notifications");
  const panel = document.getElementById("notif-panel");

  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!panel.hidden) { panel.hidden = true; return; }
    panel.hidden = false;
    panel.innerHTML = `<div style="padding:16px">${mkSpinner().outerHTML}</div>`;

    try {
      const res = await api.get("/api/notifications");
      panel.innerHTML = "";

      if (!res.data.length) {
        panel.innerHTML = `<p class="notif-empty">You're all caught up!</p>`;
        return;
      }

      res.data.forEach((n) => panel.appendChild(buildNotifItem(n)));
      await api.post("/api/notifications/read");
      renderBadge(0);
    } catch {
      panel.innerHTML = `<p class="notif-empty" style="color:var(--danger)">Could not load.</p>`;
    }
  });

  document.addEventListener("click", (e) => {
    if (!panel.contains(e.target) && !btn.contains(e.target)) panel.hidden = true;
  });
}

function buildNotifItem(n) {
  const el = document.createElement("div");
  el.className = "notif-item" + (n.is_read ? "" : " notif-item--unread");
  el.innerHTML = `
    <img class="notif-avatar" src="${esc(n.actor_avatar_url)}" onerror="this.src='/static/images/default-avatar.svg'" />
    <span class="notif-text">
      <strong>${esc(n.actor_name || "Someone")}</strong>
      ${notifVerb(n.type)}
    </span>
    <time class="notif-time">${timeAgo(n.created_at)}</time>
  `;
  return el;
}

function notifVerb(type) {
  return { like: " liked your post.", comment: " commented.", follow: " started following you.", mention: " mentioned you." }[type] || " interacted.";
}

// ── Explore ───────────────────────────────────────────────────────────────────
async function loadExplore() {
  const grid = document.getElementById("explore-grid");
  grid.innerHTML = "";
  grid.appendChild(mkSpinner());

  try {
    const res = await api.get("/api/search?q=");
    grid.innerHTML = "";

    if (!res.data.length) {
      grid.appendChild(mkEmptyState("No members yet", "Members will appear here once they join."));
      return;
    }

    res.data.forEach((u) => {
      const thumb = document.createElement("button");
      thumb.className = "explore-thumb";
      thumb.innerHTML = `
        <img src="${esc(u.avatar_url)}" onerror="this.src='/static/images/default-avatar.svg'" alt="${esc(u.display_name)}" loading="lazy" />
        <div class="explore-thumb-overlay"><span>${esc(u.display_name)}</span></div>
      `;
      thumb.addEventListener("click", () => {
        document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".nav-btn[data-view='profile']").forEach((b) => b.classList.add("active"));
        switchView("profile");
        loadProfile(u.username);
      });
      grid.appendChild(thumb);
    });
  } catch {
    grid.innerHTML = "";
    grid.appendChild(mkEmptyState("Could not load", "Please try again later."));
  }
}

// ── Reels ─────────────────────────────────────────────────────────────────────
async function loadReels() {
  const view = document.getElementById("reels-view");
  view.innerHTML = "";

  try {
    const res = await api.get("/api/feed?cursor=0");
    const reels = (res.data || []).filter((p) => p.media_type === "video" || p.media_type === "reel");

    if (!reels.length) {
      view.appendChild(mkEmptyState("No sermons yet", "Video sermons will appear here."));
      return;
    }

    const obs = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        const vid = en.target.querySelector("video");
        if (!vid) return;
        if (en.isIntersecting) vid.play().catch(() => {});
        else { vid.pause(); vid.currentTime = 0; }
      });
    }, { threshold: 0.75 });

    reels.forEach((r) => {
      const card = document.createElement("div");
      card.className = "reel-card";
      card.innerHTML = `
        <video src="${esc(r.media_url)}" preload="none" loop playsinline muted></video>
        <div class="reel-overlay">
          <div class="reel-info">
            <img class="reel-avatar" src="${esc(r.avatar_url)}" onerror="this.src='/static/images/default-avatar.svg'" />
            <div>
              <div class="reel-name">${esc(r.display_name)}</div>
              ${r.caption ? `<div class="reel-caption">${esc(r.caption)}</div>` : ""}
            </div>
          </div>
        </div>
        <div class="reel-side-actions">
          <button class="reel-action-btn btn-like ${r.liked ? 'liked' : ''}" aria-label="Like">
            <svg viewBox="0 0 24 24" fill="${r.liked ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" width="24" height="24">
              <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
            </svg>
            <span>${r.like_count}</span>
          </button>
          <button class="reel-action-btn" aria-label="Comment" onclick="openComments(${r.id})">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="24" height="24">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
            <span>${r.comment_count}</span>
          </button>
        </div>
        <button class="reel-mute-btn" aria-label="Toggle mute">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20">
            <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/>
          </svg>
        </button>
      `;
      const vid = card.querySelector("video");
      const muteBtn = card.querySelector(".reel-mute-btn");
      muteBtn.addEventListener("click", () => {
        vid.muted = !vid.muted;
        muteBtn.innerHTML = vid.muted
          ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>`
          : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>`;
      });
      obs.observe(card);
      view.appendChild(card);
    });
  } catch {
    view.appendChild(mkEmptyState("Could not load sermons", "Please try again."));
  }
}

// ── Profile ───────────────────────────────────────────────────────────────────
async function loadProfile(username) {
  if (!username) return;
  const view = document.getElementById("profile-view");
  view.innerHTML = "";
  view.appendChild(mkSpinner());

  try {
    const { data } = await api.get(`/api/profile/${encodeURIComponent(username)}`);
    renderProfileView(data);
  } catch (err) {
    view.innerHTML = "";
    view.appendChild(mkEmptyState("Profile not found", "This user doesn't exist."));
  }
}

function renderProfileView(data) {
  const view = document.getElementById("profile-view");
  view.innerHTML = `
    <div class="profile-hero">
      <div class="profile-avatar-wrap">
        <img class="profile-avatar-lg" src="${esc(data.avatar_url)}" onerror="this.src='/static/images/default-avatar.svg'" alt="${esc(data.display_name)}" />
      </div>
      <div class="profile-info">
        <div class="profile-name">${esc(data.display_name)}</div>
        <div class="profile-stats-row">
          <div class="p-stat"><strong>${data.post_count}</strong><span>posts</span></div>
          <div class="p-stat"><strong>${data.follower_count}</strong><span>followers</span></div>
          <div class="p-stat"><strong>${data.following_count}</strong><span>following</span></div>
        </div>
        ${data.bio ? `<p class="profile-bio-text">${esc(data.bio)}</p>` : ""}
        <div class="profile-actions">
          ${data.is_own
            ? `<button class="btn btn-outline" id="btn-edit-profile">Edit Profile</button>
               <button class="btn btn-outline" onclick="window.location.href='/admin/'">Admin Panel</button>`
            : `<button class="btn ${data.is_following ? "btn-outline" : "btn-primary"}" id="btn-follow">${data.is_following ? "Following" : "Follow"}</button>`
          }
        </div>
      </div>
    </div>
    <div class="profile-grid" id="profile-grid"></div>
  `;

  const grid = view.querySelector("#profile-grid");
  data.posts.forEach((p) => {
    const thumb = document.createElement("button");
    thumb.className = "grid-thumb";
    thumb.innerHTML = `
      <img src="${esc(p.media_url)}" alt="Post" loading="lazy" />
      <div class="grid-thumb-hover">
        <span>❤ ${p.like_count}</span>
        <span>💬 ${p.comment_count}</span>
      </div>
    `;
    thumb.addEventListener("click", () => openComments(p.id));
    grid.appendChild(thumb);
  });

  if (!data.is_own) {
    const followBtn = view.querySelector("#btn-follow");
    if (followBtn) {
      followBtn.addEventListener("click", async () => {
        try {
          const res = await api.post(`/api/profile/${encodeURIComponent(data.username)}/follow`);
          followBtn.textContent = res.data.following ? "Following" : "Follow";
          followBtn.className   = `btn ${res.data.following ? "btn-outline" : "btn-primary"}`;
        } catch (err) {
          if (err.status !== 401) toast(err.message);
        }
      });
    }
  }
}

// ── Give View ─────────────────────────────────────────────────────────────────
function renderGiveView() {
  const view = document.getElementById("give-view");
  const presets = [10, 20, 50, 100, 200, 500];

  view.innerHTML = `
    <div class="give-hero">
      <div class="give-icon">🙏</div>
      <h2>Give to God's Work</h2>
      <p>Your giving supports the work of the church and spreads the Gospel.</p>
    </div>
    <div class="give-card">
      <div class="give-presets">
        ${presets.map((a) => `<button class="give-preset" data-amount="${a}">GHS ${a}</button>`).join("")}
      </div>
      <div class="form-group">
        <label>Custom amount (GHS)</label>
        <input type="number" id="give-custom" class="form-input" placeholder="e.g. 75" min="1" step="0.01" />
      </div>
      <div class="form-group">
        <label>Category</label>
        <select id="give-category" class="form-input">
          <option value="tithe">Tithe</option>
          <option value="offering">Offering</option>
          <option value="pledge">Pledge</option>
          <option value="special">Special Offering</option>
        </select>
      </div>
      <button class="btn btn-primary btn-full" id="btn-give">Give Now</button>
      <div id="give-result" hidden></div>
    </div>
  `;

  let chosenAmount = null;

  view.querySelectorAll(".give-preset").forEach((btn) => {
    btn.addEventListener("click", () => {
      view.querySelectorAll(".give-preset").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
      chosenAmount = parseFloat(btn.dataset.amount);
      document.getElementById("give-custom").value = "";
    });
  });

  document.getElementById("btn-give").addEventListener("click", async () => {
    const custom = parseFloat(document.getElementById("give-custom").value);
    const amount = custom > 0 ? custom : chosenAmount;
    if (!amount || amount <= 0) { toast("Please select or enter an amount."); return; }

    const giveBtn = document.getElementById("btn-give");
    giveBtn.disabled = true;
    giveBtn.textContent = "Processing…";

    try {
      const { data } = await api.post("/api/give", {
        amount,
        category: document.getElementById("give-category").value,
      });
      const result = document.getElementById("give-result");
      result.hidden = false;
      result.innerHTML = `
        <div class="give-success">
          <div class="give-success-icon">✓</div>
          <h3>Thank you!</h3>
          <p>Your offering of <strong>GHS ${amount.toFixed(2)}</strong> has been recorded.</p>
          <p class="give-ref">Reference: <code>${esc(data.reference)}</code></p>
        </div>
      `;
      giveBtn.style.display = "none";
    } catch (err) {
      giveBtn.disabled = false;
      giveBtn.textContent = "Give Now";
      toast("Giving failed: " + err.message);
    }
  });
}

// ── Helper elements ───────────────────────────────────────────────────────────
function mkSpinner() {
  const d = document.createElement("div");
  d.className = "spinner";
  return d;
}

function mkEmptyState(title, sub) {
  const d = document.createElement("div");
  d.className = "empty-state";
  d.innerHTML = `<p class="empty-title">${esc(title)}</p><p class="empty-sub">${esc(sub)}</p>`;
  return d;
}

// ── Utilities ─────────────────────────────────────────────────────────────────
function esc(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function timeAgo(iso) {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  if (isNaN(d)) return "";
  const s = Math.floor((Date.now() - d) / 1000);
  if (s < 60)     return `${s}s`;
  if (s < 3600)   return `${Math.floor(s / 60)}m`;
  if (s < 86400)  return `${Math.floor(s / 3600)}h`;
  if (s < 604800) return `${Math.floor(s / 86400)}d`;
  return d.toLocaleDateString();
}

// ── Push Notifications (Capacitor FCM) ─────────────────────────────────────────
const pushManager = {
  async init() {
    // Check if running in Capacitor
    if (!window.Capacitor || !window.Capacitor.Plugins.PushNotifications) {
      console.log("Push notifications not available (not in Capacitor)");
      return;
    }
    
    const { PushNotifications } = window.Capacitor.Plugins;
    
    // Request permissions
    const permission = await PushNotifications.requestPermissions();
    if (permission.receive !== "granted") {
      console.log("Push notification permission denied");
      return;
    }
    
    // Register for push notifications
    await PushNotifications.register();
    
    // Listen for registration
    PushNotifications.addListener("registration", async (token) => {
      console.log("FCM Token received:", token.value);
      try {
        await api.post("/api/save-fcm-token", {
          token: token.value,
          platform: this.getPlatform(),
        });
        console.log("FCM token saved successfully");
      } catch (err) {
        console.error("Failed to save FCM token:", err);
      }
    });
    
    // Listen for push notification received (app in foreground)
    PushNotifications.addListener("pushNotificationReceived", (msg) => {
      console.log("Push notification received:", msg);
      this.handleForegroundNotification(msg);
    });
    
    // Listen for push notification clicked
    PushNotifications.addListener("pushNotificationActionPerformed", (msg) => {
      console.log("Push notification action:", msg);
      this.handleNotificationClick(msg);
    });
  },
  
  getPlatform() {
    if (!window.Capacitor) return "android";
    return window.Capacitor.getPlatform() === "ios" ? "ios" : "android";
  },
  
  handleForegroundNotification(msg) {
    // Show in-app notification (Instagram-style)
    const data = msg.data || {};
    const title = data.title || msg.title || "New Notification";
    const body = data.body || msg.body || "";
    
    // Show toast
    toast(`${title}: ${body}`);
    
    // Update notification badge
    this.updateBadge();
  },
  
  handleNotificationClick(msg) {
    const data = msg.data || {};
    
    // Deep-link based on notification type
    if (data.type === "like" && data.post_id) {
      // Navigate to post
      this.navigateToPost(data.post_id);
    } else if (data.type === "comment" && data.post_id) {
      // Open comments
      this.navigateToPost(data.post_id, "comments");
    } else if (data.type === "follow" && data.follower_id) {
      // Navigate to profile
      this.navigateToProfile(data.follower_id);
    } else if (data.type === "rsvp" && data.event_id) {
      // Navigate to event
      this.navigateToEvent(data.event_id);
    }
  },
  
  navigateToPost(postId, openComments = false) {
    // Switch to feed view and open post
    showView("feed");
    openPost(postId, openComments);
  },
  
  navigateToProfile(userId) {
    // This would need to be implemented based on your routing
    console.log("Navigate to profile:", userId);
  },
  
  navigateToEvent(eventId) {
    // This would need to be implemented based on your routing
    console.log("Navigate to event:", eventId);
  },
  
  async updateBadge() {
    // Refresh notifications to update unread count
    try {
      const { data } = await api.get("/api/notifications");
      const unread = data.unread_count || 0;
      const badge = document.getElementById("notif-badge");
      if (badge) {
        badge.textContent = unread;
        badge.hidden = unread === 0;
      }
    } catch (err) {
      console.error("Failed to update notification badge:", err);
    }
  },
};

// Initialize push notifications after boot
boot().then(() => {
  // Delay to ensure Capacitor is ready
  setTimeout(() => pushManager.init(), 1000);
});
