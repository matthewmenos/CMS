'use strict';
const Stories = (() => {
  let stories=[], current=0, timer=null, progressTimer=null;
  const DURATION = 5000;

  async function init(){
    const track=document.getElementById('storiesTrack'); if(!track)return;
    try{
      const[storiesData,devData]=await Promise.all([API.get('/stories'),API.get('/devotional/today')]);
      stories=storiesData.stories||[];
      renderStoriesBar(track,stories,devData.devotional);
      if(devData.devotional) setupDevotionalStory(devData.devotional);
    }catch{/* silent */}
  }

  function renderStoriesBar(track,storiesList,devotional){
    const existing=track.querySelectorAll('.story-item:not(.story-devotional)');
    existing.forEach(e=>e.remove());
    // Group by member
    const byMember={};
    storiesList.forEach(s=>{
      const id=s.author?.id||'anon';
      if(!byMember[id]){byMember[id]={author:s.author,stories:[]};}
      byMember[id].stories.push(s);
    });
    Object.values(byMember).forEach((grp,idx)=>{
      const item=document.createElement('div');
      item.className='story-item';
      item.setAttribute('role','button'); item.setAttribute('tabindex','0');
      const av=grp.author?.avatar_url||'/static/icons/logo.png';
      const nm=grp.author?.username||'Member';
      item.innerHTML=`<div class="story-ring"><div class="story-avatar-wrap">
        <img class="story-avatar" src="${av}" alt="${nm}" loading="lazy" onerror="this.src='/static/icons/logo.png'"/>
        </div></div><span class="story-label">${nm}</span>`;
      item.addEventListener('click',()=>openStoryGroup(grp.stories));
      item.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' ')openStoryGroup(grp.stories);});
      track.appendChild(item);
    });
  }

  function setupDevotionalStory(devotional){
    const el=document.getElementById('devotionalStory');
    if(!el)return;
    const fakeStory={id:'dev',media_url:devotional.cover_image_url||'/static/icons/logo.png',
      media_type:'image',caption:`${devotional.title}\n\n${devotional.scripture||''}`,
      author:{username:'Daily Word',avatar_url:'/static/icons/logo.png'},created_at:devotional.created_at};
    el.addEventListener('click',()=>openStoryGroup([fakeStory]));
    el.setAttribute('role','button'); el.setAttribute('tabindex','0');
  }

  function openStoryGroup(storyList){
    stories=storyList; current=0; showStory(0);
    document.getElementById('storyModal').hidden=false;
  }

  function showStory(idx){
    if(idx>=stories.length){closeModal();return;}
    const s=stories[idx];
    const img=document.getElementById('storyMediaImg');
    const cap=document.getElementById('storyCaption');
    const av=document.getElementById('smAvatar');
    const nm=document.getElementById('smName');
    const ti=document.getElementById('smTime');
    const prog=document.getElementById('storyProgress');
    const bg=document.getElementById('storyModal').querySelector('.story-modal-bg');

    img.src=s.media_url||''; cap.textContent=s.caption||'';
    av.src=s.author?.avatar_url||'/static/icons/logo.png';
    nm.textContent=s.author?.username||'Member';
    ti.textContent=s.created_at?timeAgo(s.created_at):'';
    bg.style.background=s.bg_color?s.bg_color:'rgba(0,0,0,.9)';

    // Progress bars
    prog.innerHTML='';
    stories.forEach((_,i)=>{
      const bar=document.createElement('div');bar.className='story-progress-bar';
      const fill=document.createElement('div');fill.className='story-progress-fill';
      fill.style.width=i<idx?'100%':i===idx?'0%':'0%';
      bar.appendChild(fill);prog.appendChild(bar);
    });
    clearTimeout(timer);
    const fill=prog.children[idx]?.firstChild;
    if(fill){fill.style.transition=`width ${DURATION}ms linear`;fill.style.width='100%';}
    timer=setTimeout(()=>nextStory(),DURATION);
  }

  function nextStory(){current++;showStory(current);}
  function closeModal(){
    clearTimeout(timer);
    document.getElementById('storyModal').hidden=true;
    stories=[];current=0;
  }

  function setup(){
    document.getElementById('smClose')?.addEventListener('click',closeModal);
    document.getElementById('storyModalBg')?.addEventListener('click',closeModal);
    document.getElementById('storyMediaWrap')?.addEventListener('click',e=>{
      const rect=e.currentTarget.getBoundingClientRect();
      e.clientX-rect.left<rect.width/2?(current=Math.max(0,current-1),showStory(current)):nextStory();
    });
    document.addEventListener('keydown',e=>{
      if(document.getElementById('storyModal')?.hidden)return;
      if(e.key==='ArrowRight')nextStory();
      else if(e.key==='ArrowLeft'){current=Math.max(0,current-1);showStory(current);}
      else if(e.key==='Escape')closeModal();
    });
  }

  document.addEventListener('DOMContentLoaded',()=>{setup();init();});
  return {init,openStoryGroup,closeModal};
})();
