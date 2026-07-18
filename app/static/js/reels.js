// Reels viewer JavaScript
let reels = [];
let currentReelIndex = 0;
let reelsObserver = null;

document.addEventListener('DOMContentLoaded', function() {
  loadReels();
});

async function loadReels() {
  try {
    const { data } = await api.get('/api/feed');
    reels = data.filter(p => p.media_type === 'video' || p.media_type === 'reel');
    renderReels();
    setupIntersectionObserver();
  } catch (err) {
    console.error('Failed to load reels:', err);
  }
}

function renderReels() {
  const container = document.getElementById('reels-container');
  container.innerHTML = '';
  
  reels.forEach((reel, index) => {
    const reelEl = document.createElement('div');
    reelEl.className = 'reel-item';
    reelEl.innerHTML = `
      <video class="reel-video" data-index="${index}" preload="none" loop muted playsinline>
        <source src="${reel.media_url}" type="video/mp4">
      </video>
      <div class="reel-overlay">
        <div class="reel-info">
          <img class="reel-avatar" src="${reel.avatar_url || '/static/images/default-avatar.svg'}" alt="" />
          <div>
            <div class="reel-name">${reel.display_name}</div>
            ${reel.caption ? `<div class="reel-caption">${reel.caption}</div>` : ''}
          </div>
        </div>
        <div class="reel-actions">
          <button class="reel-action-btn btn-like ${reel.liked ? 'liked' : ''}" data-post-id="${reel.id}">
            <svg viewBox="0 0 24 24" fill="${reel.liked ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2">
              <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
            </svg>
            <span class="reel-action-count">${reel.like_count}</span>
          </button>
          <button class="reel-action-btn btn-comment" data-post-id="${reel.id}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
            <span class="reel-action-count">${reel.comment_count}</span>
          </button>
        </div>
      </div>
    `;
    container.appendChild(reelEl);
  });
}

function setupIntersectionObserver() {
  const options = {
    root: document.getElementById('reels-main'),
    rootMargin: '0px',
    threshold: 0.75
  };

  reelsObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      const video = entry.target.querySelector('video');
      if (entry.isIntersecting) {
        video.play().catch(() => {});
      } else {
        video.pause();
        video.currentTime = 0;
      }
    });
  }, options);

  document.querySelectorAll('.reel-item').forEach(item => {
    reelsObserver.observe(item);
  });
}