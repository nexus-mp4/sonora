'use strict';
/* =====================================================================
   Sonora · YouTube Music source
   Talks to server.py (ytmusicapi for browsing, yt-dlp for audio).
   Shares the player, queue, lyrics and colour system with the local library,
   but keeps its own data (Y.tracks) so nothing leaks into the local views.
   Relies on globals from index.html: S, Y, TR, HAS, VIEWS, shHead, row, rows, listId, cov, detailHead ...
   ===================================================================== */
Object.assign(I,{explore:'M12 10.9c-.61 0-1.1.49-1.1 1.1s.49 1.1 1.1 1.1c.61 0 1.1-.49 1.1-1.1s-.49-1.1-1.1-1.1zM12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm2.19 12.19L6 18l3.81-8.19L18 6l-3.81 8.19z'});

const JSON_H={'Content-Type':'application/json'};

/* ---------- api ---------- */
async function yapi(path,opt){
  let r;try{r=await fetch('/api'+path,opt)}
  catch{const e=new Error('Can’t reach the Sonora server.');e.offline=true;throw e}
  let j={};try{j=await r.json()}catch{}
  if(!r.ok){const e=new Error(j.error||('HTTP '+r.status));e.auth=!!j.auth;e.status=r.status;throw e}
  return j}
function ycached(key,path,ttl=300000){
  const h=Y.cache.get(key);if(h&&Date.now()-h.t<ttl)return Promise.resolve(h.v);
  return yapi(path).then(v=>{Y.cache.set(key,{t:Date.now(),v});return v})}
const yEntity=(k,id)=>ycached(k+':'+id,`/${k}/${enc(id)}`);

async function ytInit(){try{const s=await yapi('/status');Y.auth=!!s.auth;Y.stream=!!s.stream}catch{}}

/* ---------- tracks + covers ---------- */
function yKey(url){if(!url)return'';const k='yc:'+hash(url);if(!S.thumbs.has(k))S.thumbs.set(k,{url:'/api/img?u='+enc(url),seed:null,remote:true});return k}
function regTrack(x){
  const t={id:x.id,src:'yt',vid:x.vid,url:'/api/stream/'+x.vid,title:x.title||'',artist:x.artist||'',albumArtist:x.artist||'',aa:x.artist||'Unknown artist',
    album:x.album||'',ak:'yt:'+(x.albumId||''),aid:x.albumId||'',arid:x.artistId||'',curl:x.cover||'',cover:yKey(x.cover),dur:x.dur||0,no:x.no||0,disc:0,
    year:'',genre:'',lyrics:'',meta:{g:{}},added:0,q:''};
  Y.tracks.set(t.id,t);return t}
function ytFix(t){t.cover=yKey(t.curl);t.meta=t.meta||{g:{}};Y.tracks.set(t.id,t)}
function ytHydrate(saved,sess){(saved||[]).forEach(t=>{ytFix(t);Y.saved.set(t.id,t)});(sess||[]).forEach(ytFix)}
const ytSnap=()=>S.queue.filter(id=>id.startsWith('yt:')).slice(0,400).map(id=>Y.tracks.get(id)).filter(Boolean);

async function ytSeed(key){
  const th=S.thumbs.get(key);if(!th)return null;if(th.seed||!th.remote)return th.seed||null;
  if(!th.seedP)th.seedP=(async()=>{try{
    const bmp=await createImageBitmap(await(await fetch(th.url)).blob()),c=document.createElement('canvas');c.width=c.height=48;
    const x=c.getContext('2d'),s=Math.min(bmp.width,bmp.height);x.drawImage(bmp,(bmp.width-s)/2,(bmp.height-s)/2,s,s,0,0,48,48);bmp.close?.();
    th.seed=extractSeed(x.getImageData(0,0,48,48).data);return th.seed}catch{return null}})();
  return th.seedP}

/* ---------- likes ---------- */
function ytLike(t,liked){
  liked?Y.saved.set(t.id,t):Y.saved.delete(t.id);DB.set('kv','ytsaved',[...Y.saved.values()]);
  if(Y.auth)yapi('/rate',{method:'POST',headers:JSON_H,body:JSON.stringify({vid:t.vid,rating:liked?'LIKE':'INDIFFERENT'})}).catch(e=>toast('Couldn’t sync that like: '+e.message))}

/* ---------- lyrics (YouTube Music first, LRCLIB fallback happens in getLyrics) ---------- */
async function ytLyrics(t){
  try{const j=await yapi('/lyrics/'+t.vid);
    if((j.synced&&j.synced.length)||j.plain){const rec={synced:j.synced||[],plain:j.plain||''};await DB.set('lyrics',t.id,rec);return rec}}catch{}
  return null}

/* ---------- look-ahead so the next song starts instantly ---------- */
const warmed=new Set();
function ytWarm(){const id=S.queue[S.qi+1],t=id&&Y.tracks.get(id);if(t&&t.vid&&!warmed.has(t.vid)){warmed.add(t.vid);fetch('/api/warm/'+t.vid).catch(()=>{})}}

/* ---------- cards + shelves ---------- */
function ycard(it){
  const key=yKey(it.cover),sub=esc(it.sub||''),fab=`<span class="fab" data-act="yt-play" data-k="${it.k}" data-id="${esc(it.id)}" role="button" aria-label="Play">${ic('play')}</span>`;
  const href={album:'#/ytalbum/',artist:'#/ytartist/',playlist:'#/ytplaylist/'}[it.k]+enc(it.id);
  return`<a class="card${it.k==='artist'?' round':''}" href="${href}"><div class="cv-wrap">${cov(key)}${fab}</div><div class="c-t">${esc(it.title)}</div><div class="c-s">${sub}</div></a>`}

function shelvesHtml(shelves,pfx,o={}){
  return(shelves||[]).map((s,i)=>{
    const songs=s.items.filter(x=>x.k==='song').map(x=>regTrack(x).id),others=s.items.filter(x=>x.k!=='song');
    if(!songs.length&&!others.length)return'';const nm=pfx+i;let out='';
    out+=shHead(esc(s.title||''),songs.length&&!o.vert?`<button class="btn txt" data-act="play-list" data-list="${nm}">Play all</button>`:'');
    if(others.length)out+=o.grid?`<div class="grid">${others.map(ycard).join('')}</div>`:`<div class="shelf">${others.map(ycard).join('')}</div>`;
    if(songs.length)out+=o.vert?`<div class="pad" style="padding-top:0">${rows(songs,nm)}</div>`
      :(listId(nm,songs.slice(0,16)),`<div class="quick list" data-list="${nm}">${songs.slice(0,16).map((id,i)=>row(TR(id),i)).join('')}</div>`);
    return out}).join('')}

/* ---------- states ---------- */
function ytLoading(){return`<div class="view-t"><div class="eyebrow">YouTube Music</div><h1 class="page">Loading</h1></div><div class="pad"><span class="statusbtn" style="display:inline-flex"><span class="spinner"></span>Fetching from YouTube Music</span></div>`}
function ytError(e){
  const head=(t,s,btn)=>`<div class="view-t"><div class="eyebrow">YouTube Music</div><h1 class="page">${t}</h1><p class="sub" style="max-width:60ch">${s}</p><div class="acts">${btn}</div></div>`;
  if(e&&e.offline)return head('Server not running','Sonora needs its small Python server for YouTube Music. In the Sonora folder run <b>python3 server.py</b>, then open <b>http://localhost:8000</b>.',
    `<button class="btn pri" data-act="retry">Try again</button><a class="btn ton" href="#/home">Use local library</a>`);
  if(e&&e.auth)return head('Sign in needed','This part of YouTube Music belongs to your account. Add it in Settings.',`<button class="btn pri" data-act="yt-open-auth">Open settings</button>`);
  return head('That didn’t load',esc((e&&e.message)||'Unknown error'),`<button class="btn pri" data-act="retry">Retry</button>`)}

/* ---------- views ---------- */
async function vYtHome(){
  const j=await ycached('home','/home',600000);
  const h=new Date().getHours(),greet=h<5?'Still up?':h<12?'Good morning':h<18?'Good afternoon':'Good evening';
  const body=shelvesHtml(j.shelves,'yh');
  return`<div class="view-t"><div class="eyebrow">YouTube Music</div><h1 class="page">${greet}</h1></div>`+(body||'<div class="pad"><p class="empty">YouTube Music returned nothing for the home page. Try Explore or search.</p></div>')+'<div style="height:28px"></div>'}

async function vYtExplore(){
  const j=await ycached('explore','/explore',1800000);
  const moods=(j.moods||[]).map(g=>shHead(esc(g.group))+`<div class="chips" style="flex-wrap:wrap;overflow:visible">${g.items.map(m=>`<a class="chip" href="#/ytmood/${enc(m.params)}/${enc(m.title)}">${esc(m.title)}</a>`).join('')}</div>`).join('');
  return`<div class="view-t"><div class="eyebrow">YouTube Music</div><h1 class="page">Explore</h1></div>`+shelvesHtml(j.shelves,'ye')+moods+'<div style="height:28px"></div>'}

async function vYtMood(params,title){
  const j=await ycached('mood:'+params,'/mood/'+enc(params));
  return`<div class="view-t"><div class="eyebrow">Explore</div><h1 class="page">${esc(title||'Mood')}</h1></div><div class="grid">${(j.items||[]).map(ycard).join('')}</div>`}

const YF=[['','All'],['songs','Songs'],['albums','Albums'],['artists','Artists'],['playlists','Playlists'],['videos','Videos']];
async function vYtSearch(q){
  q=(q||'').trim();
  const head=`<div class="view-t"><div class="eyebrow">YouTube Music</div><h1 class="page">${q?'“'+esc(q)+'”':'Search'}</h1></div>`;
  if(!q)return head+'<div class="pad"><p class="empty">Search songs, albums, artists and playlists on YouTube Music.</p></div>';
  const f=Y.f||'';
  const j=await ycached(`s:${f}:${q}`,`/search?q=${enc(q)}${f?'&filter='+f:''}`,120000);
  const chips=`<div class="chips" role="group" aria-label="Filter results">${YF.map(([k,l])=>`<button class="chip${f===k?' on':''}" data-act="yt-filter" data-f="${k}">${l}</button>`).join('')}</div>`;
  const body=shelvesHtml(j.shelves,'ys',{vert:true,grid:!!f&&f!=='songs'&&f!=='videos'});
  return head+chips+(body||`<div class="pad"><p class="empty">Nothing found for “${esc(q)}”.</p></div>`)+'<div style="height:28px"></div>'}

async function vYtAlbum(id){
  const a=await yEntity('album',id),ids=a.tracks.map(x=>regTrack(x).id),ck=yKey(a.cover);listId('yalbum',ids);
  const by=a.artistId?`<a href="#/ytartist/${enc(a.artistId)}">${esc(a.artist)}</a>`:esc(a.artist);
  return detailHead('Album',a.title,cov(ck),`${by}${a.year?' · '+esc(a.year):''} · ${ids.length} songs${a.dur?' · '+fmtLong(a.dur):''}`,ck,'yalbum')+`<div class="pad">${rows(ids,'yalbum',{num:true})}</div>`}

async function vYtArtist(id){
  const a=await yEntity('artist',id),ids=(a.songs||[]).map(x=>regTrack(x).id),ck=yKey(a.cover);listId('yartist',ids);
  const sec=(t,l)=>l&&l.length?shHead(t)+`<div class="shelf">${l.map(ycard).join('')}</div>`:'';
  return detailHead('Artist',a.name,cov(ck),esc(a.sub||''),ck,'yartist')+(ids.length?shHead('Songs')+`<div class="pad">${rows(ids,'yartist')}</div>`:'')+
    sec('Albums',a.albums)+sec('Singles & EPs',a.singles)+sec('Fans might also like',a.related)+'<div style="height:28px"></div>'}

async function vYtPlaylist(id){
  const p=await yEntity('playlist',id),ids=p.tracks.map(x=>regTrack(x).id),ck=yKey(p.cover);listId('yplaylist',ids);
  if(id==='LM'&&ids.length){ids.forEach(i=>S.likes.add(i));DB.set('kv','likes',[...S.likes])}
  return detailHead('Playlist',p.title,cov(ck),`${esc(p.author||'')}${p.author?' · ':''}${ids.length} songs${p.dur?' · '+fmtLong(p.dur):''}`,ck,'yplaylist')+
    `<div class="pad">${ids.length?rows(ids,'yplaylist'):'<p class="empty">This playlist is empty.</p>'}</div>`}

function vYtSignedOut(){
  const ids=[...Y.saved.keys()];listId('ysaved',ids);
  return`<div class="view-t"><div class="eyebrow">YouTube Music</div><h1 class="page">Library</h1><p class="sub" style="max-width:58ch">Sign in to see your playlists, albums and subscriptions, and to sync likes with your account. Hearts still work without an account and are kept on this device.</p>
  <div class="acts"><button class="btn pri" data-act="yt-open-auth">Sign in</button></div></div>`+
  (ids.length?shHead('Saved on this device',`<button class="btn txt" data-act="play-list" data-list="ysaved">Play all</button>`)+`<div class="pad">${rows(ids,'ysaved')}</div>`:'')}
async function vYtLibrary(){
  let j;try{j=await ycached('lib','/library',120000)}catch(e){if(e.auth)return vYtSignedOut();throw e}
  Y.auth=true;
  const liked=`<a class="card" href="#/ytplaylist/LM"><div class="cv-wrap"><div class="cv ph">${ic('heart')}</div></div><div class="c-t">Liked songs</div><div class="c-s">Your YouTube Music likes</div></a>`;
  const sec=(t,l)=>l&&l.length?shHead(t)+`<div class="shelf">${l.map(ycard).join('')}</div>`:'';
  return`<div class="view-t"><div class="eyebrow">YouTube Music</div><h1 class="page">Library</h1></div>`+shHead('Playlists')+`<div class="grid">${liked}${(j.playlists||[]).map(ycard).join('')}</div>`+
    sec('Albums',j.albums)+sec('Subscriptions',j.artists)+'<div style="height:28px"></div>'}

Object.assign(VIEWS,{yt:vYtHome,ytexplore:vYtExplore,ytmood:vYtMood,ytsearch:vYtSearch,ytalbum:vYtAlbum,ytartist:vYtArtist,ytplaylist:vYtPlaylist,ytlibrary:vYtLibrary});

/* ---------- playing things ---------- */
async function ytPlayEntity(k,id){
  try{const j=await yEntity(k,id),list=(k==='artist'?j.songs:j.tracks)||[],ids=list.map(x=>regTrack(x).id);
    if(!ids.length){toast('Nothing to play there.');return}
    playList(ids,0,{shuffle:false,ctx:j.title||j.name||''})}catch(e){toast(e.message)}}
async function ytRadio(t){
  try{toast('Starting radio…');const j=await yapi('/watch/'+t.vid),ids=j.tracks.map(x=>regTrack(x).id);
    if(!ids.length){toast('No radio for that song.');return}playList(ids,0,{shuffle:false,ctx:'Radio'})}catch(e){toast(e.message)}}

function ytTrackMenu(t,r){
  const liked=S.likes.has(t.id);
  openMenu([{l:'Play next',i:'next',f:()=>enqueue([t.id],true)},{l:'Add to queue',i:'queue',f:()=>enqueue([t.id])},{l:'Start radio',i:'sync',f:()=>ytRadio(t)},
    {l:'Save to YouTube playlist',i:'plus',f:()=>setTimeout(()=>ytPlaylistMenu(t,r),0)},
    {l:liked?'Remove from liked':'Add to liked',i:liked?'heart':'heart0',f:()=>toggleLike(t.id)},
    ...(t.aid||t.arid?['-']:[]),
    ...(t.aid?[{l:'Go to album',i:'album',f:()=>{location.hash='#/ytalbum/'+enc(t.aid)}}]:[]),
    ...(t.arid?[{l:'Go to artist',i:'person',f:()=>{location.hash='#/ytartist/'+enc(t.arid)}}]:[])],r.right,r.bottom,true)}
async function ytPlaylistMenu(t,r){
  if(!Y.auth){toast('Sign in to YouTube Music in Settings to save to playlists.');return}
  try{const pls=(await ycached('lib','/library',120000)).playlists||[];if(!pls.length){toast('No playlists in your library yet.');return}
    openMenu(pls.slice(0,30).map(p=>({l:p.title,i:'queue',f:()=>yapi('/playlist/add',{method:'POST',headers:JSON_H,body:JSON.stringify({playlistId:p.id,vid:t.vid})})
      .then(()=>{toast('Saved to '+p.title);Y.cache.delete('playlist:'+p.id)}).catch(e=>toast(e.message))})),r.right,r.bottom,true)}catch(e){toast(e.message)}}

function ytAct(act,el){
  switch(act){
    case'yt-play':ytPlayEntity(el.dataset.k,el.dataset.id);break;
    case'retry':Y.cache.clear();route();break;
    case'yt-filter':Y.f=el.dataset.f;route(true);break;
    case'yt-open-auth':openSettings();setTimeout(()=>{const b=$('#yt-auth-box');if(b&&!Y.auth)b.hidden=false},60);break}}

/* ---------- settings block ---------- */
function ytSettingsHtml(){
  return`<div class="setrow"><div><b>YouTube Music</b><small id="yt-stat">Checking server…</small></div><button class="btn ton" id="yt-auth-btn" hidden>Sign in</button></div>
  <div id="yt-auth-box" hidden><div class="fld"><label for="yt-hdr">Request headers from music.youtube.com</label><textarea id="yt-hdr" spellcheck="false" placeholder="Paste the raw request headers here"></textarea></div>
  <p class="sub" style="font-size:12px;margin:-4px 0 12px">Open music.youtube.com while signed in, press F12, go to Network, filter for “browse”, click a POST request and copy its request headers. They are stored in browser.json next to server.py and never leave your computer.</p>
  <div class="dlg-acts" style="margin:0 0 8px"><button class="btn pri" id="yt-auth-save">Save</button></div></div>`}
function ytBindSettings(){
  const stat=$('#yt-stat'),btn=$('#yt-auth-btn'),box=$('#yt-auth-box');if(!stat)return;
  const show=(auth,extra='')=>{Y.auth=auth;stat.textContent=(auth?'Signed in':'Not signed in · browsing as guest')+extra;btn.hidden=false;btn.textContent=auth?'Sign out':'Sign in'};
  yapi('/status').then(s=>show(!!s.auth,s.stream?'':' · yt-dlp is missing, so songs can’t play')).catch(()=>{stat.textContent='Server not running. Start it with python3 server.py'});
  btn.onclick=async()=>{if(Y.auth){try{await yapi('/auth',{method:'DELETE'});Y.cache.clear();show(false);toast('Signed out');route(true)}catch(e){toast(e.message)}}else box.hidden=!box.hidden};
  $('#yt-auth-save').onclick=async e=>{const b=e.currentTarget;b.disabled=true;
    try{const r=await yapi('/auth',{method:'POST',headers:JSON_H,body:JSON.stringify({headers:$('#yt-hdr').value})});Y.cache.clear();box.hidden=true;show(true,r.account?' as '+r.account:'');toast('Signed in to YouTube Music');route(true)}
    catch(err){toast(err.message)}b.disabled=false}}
