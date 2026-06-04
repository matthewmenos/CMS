/**
 * app.js — Core utilities for COP Agona Ahanta ChMS
 *
 * Load order on feed.html:
 *   1. Inline <script> sets window.APP_CONFIG (synchronous, before this file)
 *   2. app.js loaded (not deferred) — API, Theme, Toast all available immediately
 *   3. stories.js, feed.js loaded — use API / Toast from above
 */
'use strict';

/* ── API Client ────────────────────────────────────────────────────────────── */
const API = (() => {
  const BASE = '/api/v1';

  function getToken() {
    // Read at call-time so we always get the latest token
    return (window.APP_CONFIG && window.APP_CONFIG.token)
      || localStorage.getItem('access_token')
      || '';
  }

  function getSlug() {
    return (window.APP_CONFIG && window.APP_CONFIG.churchSlug) || '';
  }

  function buildHeaders(extra) {
    var h = {
      'Content-Type': 'application/json',
      'X-Church-Slug': getSlug(),
    };
    var token = getToken();
    if (token) h['Authorization'] = 'Bearer ' + token;
    if (extra) Object.assign(h, extra);
    return h;
  }

  async function request(method, path, body, opts) {
    opts = opts || {};
    var fetchOpts = {
      method: method,
      headers: buildHeaders(opts.headers),
    };
    if (body !== null && body !== undefined) {
      fetchOpts.body = JSON.stringify(body);
    }
    if (opts.signal) fetchOpts.signal = opts.signal;

    var res;
    try {
      res = await fetch(BASE + path, fetchOpts);
    } catch (networkErr) {
      throw new Error('Network error: ' + networkErr.message);
    }

    // Token expired or invalid → clear and redirect to login
    if (res.status === 401) {
      localStorage.removeItem('access_token');
      localStorage.removeItem('user');
      window.location.replace('/auth/login');
      return;
    }

    var data;
    try { data = await res.json(); }
    catch (e) { data = {}; }

    if (!res.ok) {
      var err = new Error(data.error || ('HTTP ' + res.status));
      err.data   = data;
      err.status = res.status;
      throw err;
    }

    return data;
  }

  return {
    get:    function(path, opts)       { return request('GET',    path, null, opts); },
    post:   function(path, body, opts) { return request('POST',   path, body, opts); },
    patch:  function(path, body, opts) { return request('PATCH',  path, body, opts); },
    delete: function(path, opts)       { return request('DELETE', path, null, opts); },
  };
})();


/* ── Theme Manager ─────────────────────────────────────────────────────────── */
const Theme = (() => {
  var KEY  = 'cop_theme';
  var root = document.documentElement;

  function get() {
    return localStorage.getItem(KEY)
      || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  }

  function set(theme) {
    root.setAttribute('data-theme', theme);
    localStorage.setItem(KEY, theme);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', theme === 'dark' ? '#0d1117' : '#1A6DFF');
  }

  function toggle() { set(get() === 'dark' ? 'light' : 'dark'); }

  function init() {
    set(get());
    var btn = document.getElementById('themeToggleBtn');
    if (btn) btn.addEventListener('click', toggle);
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(e) {
      if (!localStorage.getItem(KEY)) set(e.matches ? 'dark' : 'light');
    });
  }

  return { init: init, get: get, set: set, toggle: toggle };
})();


/* ── Toast Notifications ───────────────────────────────────────────────────── */
const Toast = (() => {
  var container = null;

  function getContainer() {
    if (!container) {
      container = document.createElement('div');
      container.className = 'toast-container';
      container.setAttribute('aria-live', 'polite');
      document.body.appendChild(container);
    }
    return container;
  }

  function show(message, type, duration) {
    type     = type     || 'info';
    duration = duration || 3000;
    var toast = document.createElement('div');
    toast.className = 'toast ' + type;
    toast.textContent = message;
    getContainer().appendChild(toast);
    setTimeout(function() {
      toast.style.opacity = '0';
      toast.style.transition = 'opacity .25s';
      setTimeout(function() { toast.remove(); }, 260);
    }, duration);
  }

  return {
    success: function(m, d) { show(m, 'success', d); },
    error:   function(m, d) { show(m, 'error',   d); },
    info:    function(m, d) { show(m, 'info',     d); },
  };
})();


/* ── Time Helpers ──────────────────────────────────────────────────────────── */
function timeAgo(isoString) {
  var diff = Math.floor((Date.now() - new Date(isoString).getTime()) / 1000);
  if (diff < 60)     return 'Just now';
  if (diff < 3600)   return Math.floor(diff / 60)    + 'm ago';
  if (diff < 86400)  return Math.floor(diff / 3600)  + 'h ago';
  if (diff < 604800) return Math.floor(diff / 86400) + 'd ago';
  return new Date(isoString).toLocaleDateString('en-GH', { month: 'short', day: 'numeric' });
}

function formatCount(n) {
  n = n || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return String(n);
}


/* ── Direct-to-R2 Upload ───────────────────────────────────────────────────── */
async function uploadToR2(file, category, onProgress) {
  category = category || 'posts';
  var resp = await API.post('/upload/presign', {
    content_type:   file.type,
    media_category: category,
  });

  await new Promise(function(resolve, reject) {
    var xhr = new XMLHttpRequest();
    xhr.open('PUT', resp.upload_url);
    xhr.setRequestHeader('Content-Type', file.type);
    if (onProgress) {
      xhr.upload.addEventListener('progress', function(e) {
        if (e.lengthComputable) onProgress(e.loaded / e.total);
      });
    }
    xhr.onload  = function() { xhr.status < 300 ? resolve() : reject(new Error('R2 upload failed: ' + xhr.status)); };
    xhr.onerror = function() { reject(new Error('Network error during upload')); };
    xhr.send(file);
  });

  return resp.object_key;
}


/* ── Notification Badge ────────────────────────────────────────────────────── */
const Notifications = (() => {
  async function load() {
    try {
      var data  = await API.get('/notifications?page=1');
      var badge = document.getElementById('notifBadge');
      if (!badge) return;
      var count = data.unread_count || 0;
      badge.textContent = count > 99 ? '99+' : count;
      badge.hidden = count === 0;
    } catch (e) { /* silent — non-critical */ }
  }
  return { load: load };
})();


/* ── Initialise ────────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', function() {
  Theme.init();
  // Only load notifications if we're on an app page (token exists)
  if (window.APP_CONFIG && window.APP_CONFIG.token) {
    Notifications.load();
    setInterval(Notifications.load, 60000);
  }
});
