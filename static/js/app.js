'use strict';
const API = (() => {
  const BASE = '/api/v1';
  const getToken = () => localStorage.getItem('access_token');
  const headers  = (extra={}) => {
    const h = {'Content-Type':'application/json','X-Church-Slug':window.APP_CONFIG?.churchSlug||'',...extra};
    const t = getToken(); if(t) h['Authorization'] = `Bearer ${t}`; return h;
  };
  async function request(method, path, body=null, opts={}) {
    const res = await fetch(BASE+path,{method,headers:headers(opts.headers||{}),
      body:body?JSON.stringify(body):undefined,signal:opts.signal});
    if(res.status===401){localStorage.removeItem('access_token');localStorage.removeItem('user');
      window.location.href='/auth/login';return;}
    const data = await res.json().catch(()=>({}));
    if(!res.ok) throw Object.assign(new Error(data.error||'Request failed'),{data,status:res.status});
    return data;
  }
  return {get:(p,o)=>request('GET',p,null,o),post:(p,b,o)=>request('POST',p,b,o),
          patch:(p,b,o)=>request('PATCH',p,b,o),delete:(p,o)=>request('DELETE',p,null,o)};
})();

const Theme = (() => {
  const KEY = 'cop_theme';
  const root = document.documentElement;
  const get = () => localStorage.getItem(KEY)||(window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
  const set = t => { root.setAttribute('data-theme',t); localStorage.setItem(KEY,t);
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content',t==='dark'?'#0d1117':'#1A6DFF'); };
  const toggle = () => set(get()==='dark'?'light':'dark');
  const init = () => { set(get()); document.getElementById('themeToggleBtn')?.addEventListener('click',toggle);
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',e=>{if(!localStorage.getItem(KEY))set(e.matches?'dark':'light');}); };
  return {init,get,set,toggle};
})();

const Toast = (() => {
  let container;
  function getContainer(){if(!container){container=document.createElement('div');
    container.className='toast-container';container.setAttribute('aria-live','polite');
    document.body.appendChild(container);}return container;}
  function show(msg,type='info',dur=3000){const t=document.createElement('div');
    t.className=`toast ${type}`;t.textContent=msg;getContainer().appendChild(t);
    setTimeout(()=>{t.style.animation='toastIn .25s ease reverse';
      t.addEventListener('animationend',()=>t.remove(),{once:true});},dur);}
  return {success:(m,d)=>show(m,'success',d),error:(m,d)=>show(m,'error',d),info:(m,d)=>show(m,'info',d)};
})();

function timeAgo(iso){
  const diff=Math.floor((Date.now()-new Date(iso).getTime())/1000);
  if(diff<60)return'Just now';if(diff<3600)return Math.floor(diff/60)+'m ago';
  if(diff<86400)return Math.floor(diff/3600)+'h ago';
  if(diff<604800)return Math.floor(diff/86400)+'d ago';
  return new Date(iso).toLocaleDateString('en-GH',{month:'short',day:'numeric'});
}

function formatCount(n){
  if(n>=1e6)return(n/1e6).toFixed(1)+'M';if(n>=1e3)return(n/1e3).toFixed(1)+'K';return String(n||0);
}

const Notifications = (() => {
  async function load(){try{const d=await API.get('/notifications?page=1');
    const badge=document.getElementById('notifBadge');if(!badge)return;
    const c=d.unread_count||0;badge.textContent=c>99?'99+':c;badge.hidden=c===0;}catch{}}
  return {load};
})();

async function uploadToR2(file,category='posts',onProgress=null){
  const {upload_url,object_key}=await API.post('/upload/presign',{content_type:file.type,media_category:category});
  await new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('PUT',upload_url);
    xhr.setRequestHeader('Content-Type',file.type);
    if(onProgress)xhr.upload.addEventListener('progress',e=>{if(e.lengthComputable)onProgress(e.loaded/e.total);});
    xhr.onload=()=>xhr.status<300?resolve():reject(new Error(`R2 ${xhr.status}`));
    xhr.onerror=()=>reject(new Error('Network error'));xhr.send(file);});
  return object_key;
}

document.addEventListener('DOMContentLoaded',()=>{Theme.init();Notifications.load();setInterval(Notifications.load,60000);});
