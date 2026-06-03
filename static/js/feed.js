'use strict';
const Feed = (() => {
  let page=1,loading=false,hasMore=true,activePostId=null;
  const feedEl=document.getElementById('feed');
  const loaderEl=document.getElementById('feedLoader');
  const sentinel=document.getElementById('loadMoreSentinel');

  async function init(){if(!feedEl)return;await loadPosts();setupIO();setupDrawer();}

  async function loadPosts(){
    if(loading||!hasMore)return; loading=true;
    try{
      const d=await API.get(`/feed/posts?page=${page}&per_page=12`);
      hasMore=d.has_next; page++;
      if(page===2&&loaderEl)loaderEl.style.display='none';
      if(!d.posts.length&&page===2){renderEmpty();return;}
      const frag=document.createDocumentFragment();
      d.posts.forEach(p=>frag.appendChild(buildCard(p)));
      feedEl.appendChild(frag);
    }catch{if(page===2&&loaderEl)loaderEl.style.display='none';Toast.error('Could not load posts.');}
    finally{loading=false;}
  }

  function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
  function fmtCaption(t){return esc(t).replace(/#(\w+)/g,'<span style="color:var(--brand-blue)">#$1</span>');}

  function buildMedia(p){
    if(!p.media_url)return'';
    if(p.media_type==='video')return`<div class="post-media"><video src="${p.media_url}" poster="${p.thumbnail_url||''}" controls preload="none" playsinline></video></div>`;
    return`<div class="post-media"><img src="${p.media_url}" alt="Post" loading="lazy" onerror="this.parentElement.style.display='none'"/></div>`;
  }

  function buildCard(p){
    const el=document.createElement('article');
    el.className='post-card'; el.dataset.postId=p.id;
    const a=p.author||{},av=a.avatar_url||'/static/icons/logo.png';
    const nm=a.display_name||a.username||'Member',role=a.church_role||'';
    const lc=formatCount(p.likes_count||0),cc=formatCount(p.comments_count||0);
    el.innerHTML=`
      ${p.is_pinned?`<div class="post-pinned-bar"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5.2v6h1.6v-6H18v-2l-2-2z"/></svg> Pinned</div>`:''}
      <header class="post-header">
        <img class="avatar avatar-md" src="${av}" alt="${esc(nm)}" loading="lazy" onerror="this.src='/static/icons/logo.png'"/>
        <div class="post-author-info">
          <div class="post-author-name">${esc(nm)}${a.is_verified?`<span class="verified-badge" title="Verified"><svg width="13" height="13" viewBox="0 0 24 24" fill="var(--brand-blue)"><path d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg></span>`:''}</div>
          ${role?`<div class="post-author-role">${esc(role)}</div>`:''}
        </div>
        <button class="icon-btn" aria-label="More" style="margin-left:auto" onclick="Feed.menu(${p.id},event)">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/></svg>
        </button>
      </header>
      ${buildMedia(p)}
      <div class="post-actions">
        <button class="action-btn like-btn${p.liked?' liked':''}" data-post-id="${p.id}" data-count="${p.likes_count||0}" aria-label="Like" onclick="Feed.like(this)">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="${p.liked?'var(--like-red)':'none'}" stroke="${p.liked?'var(--like-red)':'currentColor'}" stroke-width="2"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
          <span class="like-count">${lc}</span>
        </button>
        <button class="action-btn" aria-label="Comment" onclick="Feed.comments(${p.id})">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
          <span>${cc}</span>
        </button>
        <button class="action-btn" aria-label="Share" onclick="Feed.share(${p.id})">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
        </button>
        <button class="action-btn" style="margin-left:auto" aria-label="Save">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>
        </button>
      </div>
      <div class="post-info">
        <div class="post-likes">${lc} likes</div>
        ${p.caption?`<p class="post-caption"><span style="font-weight:600">${esc(a.username||'')}</span> ${fmtCaption(p.caption)}</p>`:''}
        ${(p.comments_count||0)>0?`<button class="post-view-comments" onclick="Feed.comments(${p.id})">View all ${cc} comments</button>`:''}
        <time class="post-timestamp" datetime="${p.created_at}">${timeAgo(p.created_at)}</time>
      </div>`;
    return el;
  }

  async function like(btn){
    const id=+btn.dataset.postId,liked=btn.classList.contains('liked');
    const svg=btn.querySelector('svg'),cEl=btn.querySelector('.like-count');
    const nv=!liked,nc=Math.max(0,+btn.dataset.count+(nv?1:-1));
    btn.dataset.count=nc;btn.classList.toggle('liked',nv);
    svg.setAttribute('fill',nv?'var(--like-red)':'none');svg.setAttribute('stroke',nv?'var(--like-red)':'currentColor');
    cEl.textContent=formatCount(nc);btn.style.transform='scale(1.3)';setTimeout(()=>btn.style.transform='',150);
    try{const d=await API.post(`/feed/posts/${id}/like`);
      btn.dataset.count=d.likes_count;cEl.textContent=formatCount(d.likes_count);
      btn.closest('.post-card')?.querySelector('.post-likes')?.( el=>el.textContent=`${formatCount(d.likes_count)} likes`);}
    catch{btn.classList.toggle('liked',liked);btn.dataset.count=nc-(nv?1:-1);cEl.textContent=formatCount(+btn.dataset.count);}
  }

  function share(id){
    const url=`${location.origin}/feed#post-${id}`;
    if(navigator.share)navigator.share({title:'COP Agona Ahanta',url}).catch(()=>{});
    else navigator.clipboard.writeText(url).then(()=>Toast.success('Link copied!')).catch(()=>Toast.info(url));
  }

  function menu(id,e){e.stopPropagation();Toast.info('Post options coming soon.');}

  function setupDrawer(){
    document.getElementById('drawerOverlay')?.addEventListener('click',closeDrawer);
    document.getElementById('ciSend')?.addEventListener('click',postComment);
    document.getElementById('ciInput')?.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey)postComment();});
  }

  async function comments(id){
    activePostId=id;
    const drawer=document.getElementById('commentDrawer'),list=document.getElementById('commentsList');
    drawer.hidden=false;
    list.innerHTML='<div style="padding:24px;text-align:center;color:var(--text-muted)">Loading…</div>';
    try{
      const d=await API.get(`/feed/posts/${id}/comments`);
      list.innerHTML='';
      if(!d.comments.length){list.innerHTML='<p style="text-align:center;color:var(--text-muted);padding:24px">No comments yet.</p>';return;}
      d.comments.forEach(c=>list.appendChild(buildComment(c)));
    }catch{list.innerHTML='<p style="color:var(--danger);padding:16px">Failed to load.</p>';}
  }

  function closeDrawer(){document.getElementById('commentDrawer').hidden=true;activePostId=null;}

  function buildComment(c){
    const div=document.createElement('div');div.className='comment-item';
    const a=c.author||{};
    div.innerHTML=`<img class="avatar avatar-sm" src="${a.avatar_url||'/static/icons/logo.png'}" alt="${esc(a.username||'')}" loading="lazy" onerror="this.src='/static/icons/logo.png'"/>
      <div class="comment-body-wrap"><span class="comment-username">${esc(a.username||'Member')}</span>
      <span class="comment-text">${esc(c.body)}</span><div class="comment-meta">${timeAgo(c.created_at)}</div></div>`;
    return div;
  }

  async function postComment(){
    if(!activePostId)return;
    const inp=document.getElementById('ciInput'),body=inp.value.trim();if(!body)return;inp.value='';
    try{
      const d=await API.post(`/feed/posts/${activePostId}/comments`,{body});
      const list=document.getElementById('commentsList');
      list.querySelector('p')?.remove();list.appendChild(buildComment(d.comment));list.scrollTop=list.scrollHeight;
    }catch{Toast.error('Could not post comment.');inp.value=body;}
  }

  function setupIO(){
    if(!sentinel)return;
    new IntersectionObserver(entries=>{if(entries[0].isIntersecting&&hasMore&&!loading)loadPosts();},{rootMargin:'200px'}).observe(sentinel);
  }

  function renderEmpty(){feedEl.innerHTML=`<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;padding:64px 24px;text-align:center;gap:16px"><svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M9 21V9"/></svg><h3 style="font-size:18px;font-weight:600">No posts yet</h3><p style="color:var(--text-secondary);font-size:14px;max-width:260px">Be the first to share with the church community.</p></div>`;}

  document.addEventListener('DOMContentLoaded',init);
  return{like,comments,share,menu};
})();
