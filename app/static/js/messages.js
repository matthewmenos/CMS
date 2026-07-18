// Messages JavaScript
document.addEventListener('DOMContentLoaded', function() {
  loadConversations();
  setupNewMessageModal();
});

async function loadConversations() {
  try {
    // For now, show empty state - in production, fetch from /api/messages
    const list = document.getElementById('conversations-list');
    list.innerHTML = `
      <div class="empty-state" style="padding: 40px 20px;">
        <p class="empty-title">No messages yet</p>
        <p class="empty-sub">Start a conversation with your church family</p>
      </div>
    `;
  } catch (err) {
    console.error('Failed to load conversations:', err);
  }
}

function setupNewMessageModal() {
  const modal = document.getElementById('modal-new-message');
  const openBtn = document.getElementById('btn-new-message');
  
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
    
    const sendBtn = document.getElementById('btn-send-message');
    if (sendBtn) {
      sendBtn.addEventListener('click', async () => {
        const recipient = document.getElementById('message-recipient').value.trim();
        const body = document.getElementById('message-body').value.trim();
        
        if (!recipient || !body) {
          toast('Recipient and message are required');
          return;
        }
        
        // In production, send to /api/messages
        toast('Message sent!');
        modal.hidden = true;
      });
    }
  }
}