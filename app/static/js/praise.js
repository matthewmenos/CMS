// Prayer Wall JavaScript
document.addEventListener('DOMContentLoaded', function() {
  loadPrayerRequests();
  setupPrayerModal();
});

async function loadPrayerRequests() {
  try {
    const { data } = await api.get('/api/prayer-requests');
    renderPrayerRequests(data);
  } catch (err) {
    console.error('Failed to load prayer requests:', err);
  }
}

function renderPrayerRequests(requests) {
  const container = document.getElementById('praise-container');
  
  if (!requests || requests.length === 0) {
    container.innerHTML = `
      <div class="empty-state" style="padding: 40px 20px;">
        <p class="empty-title">No prayer requests yet</p>
        <p class="empty-sub">Be the first to share a prayer request</p>
      </div>
    `;
    return;
  }
  
  container.innerHTML = '';
  requests.forEach(req => {
    const el = document.createElement('div');
    el.className = 'prayer-card';
    el.innerHTML = `
      <div class="prayer-header">
        <img class="prayer-avatar" src="${req.avatar_url || '/static/images/default-avatar.svg'}" alt="" />
        <span class="prayer-name">${req.display_name}</span>
        <time class="prayer-time">${timeAgo(req.created_at)}</time>
      </div>
      <h3 class="prayer-title">${req.title}</h3>
      ${req.body ? `<p class="prayer-body">${req.body}</p>` : ''}
      <div class="prayer-actions">
        <button class="btn btn-primary btn-sm btn-pray" data-id="${req.id}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16">
            <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
          </svg>
          Pray
        </button>
        ${req.is_answered ? '<span class="prayer-answered">Answered</span>' : ''}
      </div>
    `;
    container.appendChild(el);
  });
}

function setupPrayerModal() {
  const modal = document.getElementById('modal-prayer');
  const openBtn = document.getElementById('btn-new-prayer');
  
  if (openBtn) {
    openBtn.addEventListener('click', () => {
      modal.hidden = false;
    });
  }
  
  if (modal) {
    modal.querySelectorAll('[data-close-modal]').forEach(btn => {
      btn.addEventListener('click', () => {
        modal.hidden = true;
      });
    });
    
    const submitBtn = document.getElementById('btn-submit-prayer');
    if (submitBtn) {
      submitBtn.addEventListener('click', async () => {
        const title = document.getElementById('prayer-title').value.trim();
        const body = document.getElementById('prayer-body').value.trim();
        const isPublic = document.getElementById('prayer-public').checked;
        
        if (!title) {
          toast('Title is required');
          return;
        }
        
        try {
          await api.post('/api/prayer-requests', {
            title,
            body,
            is_public: isPublic
          });
          toast('Prayer request posted!');
          modal.hidden = true;
          loadPrayerRequests();
        } catch (err) {
          toast('Failed to post: ' + err.message);
        }
      });
    }
  }
}