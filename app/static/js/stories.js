// Stories viewer JavaScript
document.addEventListener('DOMContentLoaded', function() {
  const urlParams = new URLSearchParams(window.location.search);
  const memberId = urlParams.get('member');
  
  if (memberId) {
    loadMemberStories(memberId);
  }
  
  const closeBtn = document.getElementById('btn-close-stories');
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      window.location.href = '/';
    });
  }
});

async function loadMemberStories(memberId) {
  try {
    const { data } = await api.get('/api/stories');
    const memberStories = data.filter(s => s.member_id == memberId);
    if (memberStories.length > 0) {
      showStory(memberStories[0]);
    }
  } catch (err) {
    console.error('Failed to load stories:', err);
  }
}

function showStory(story) {
  document.getElementById('story-avatar').src = story.avatar_url || '/static/images/default-avatar.svg';
  document.getElementById('story-username').textContent = story.display_name;
  document.getElementById('story-time').textContent = timeAgo(story.created_at);
  document.getElementById('story-caption').textContent = story.caption || '';
  
  const img = document.getElementById('story-image');
  const vid = document.getElementById('story-video');
  
  if (story.media_type === 'video') {
    img.hidden = true;
    vid.hidden = false;
    vid.src = story.media_url;
  } else {
    vid.pause();
    vid.hidden = true;
    img.hidden = false;
    img.src = story.media_url;
  }
}