'use strict';
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl = value => {try {const u = new URL(value); return u.protocol === 'https:' ? esc(u.href) : '#';} catch {return '#';}};
const techCategories = new Set(['AI / 数据', '云计算 / 安全', '研发 / 工程']);
const sourceNames = {microsoft:'微软 Microsoft', amazon:'亚马逊 / AWS', goldman:'高盛 Goldman Sachs', walmart:'沃尔玛中国', nvidia:'英伟达 NVIDIA', apple:'苹果 Apple', cisco:'思科 Cisco', meta:'Meta', airwallex:'空中云汇 Airwallex', google:'谷歌 Google', tencent:'腾讯 Tencent', alibaba:'阿里巴巴 Alibaba'};
let data = {jobs:[], companies:[], sources:[], history:[]};
let state = {tab:'jobs', city:'', companies:new Set(), category:'', type:'', query:'', quick:'all', sort:'tech', group:false, limit:25};
let saved;
try {const raw = JSON.parse(localStorage.getItem('southbound:saved') || '[]'); saved = new Set(Array.isArray(raw) ? raw.filter(x=>typeof x === 'string') : []);} catch {saved = new Set();}
let live = false, refreshing = false, toastTimer, serverMode = false;
let account = null, googleEnabled = false;
const formatDate = (value, time = false) => value ? new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',...(time?{hour:'2-digit',minute:'2-digit'}:{}),timeZone:'Asia/Shanghai'}).format(new Date(value)) : '未提供';
const today = () => new Intl.DateTimeFormat('en-CA',{year:'numeric',month:'2-digit',day:'2-digit',timeZone:'Asia/Shanghai'}).format(new Date());
const isNew = j => !j.baseline && j.firstSeenAt?.slice(0,10) === today();
const co = id => data.companies.find(c=>c.id===id);
const active = () => data.jobs.filter(j=>j.status !== 'missing');
const pin = '<svg viewBox="0 0 16 19" aria-hidden="true"><path d="M8 18S2 11 2 7a6 6 0 1 1 12 0c0 4-6 11-6 11Z"/><circle cx="8" cy="7" r="2"/></svg>';
const icons = {"refresh": "<svg class=\"ui-icon refresh-icon\" viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M20 11a8 8 0 1 0-2.4 6.6M20 4v7h-7\"/></svg>", "external": "<svg class=\"ui-icon\" viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M13 5h6v6m0-6-8 8M9 5H5v14h14v-4\"/></svg>", "chevron": "<svg class=\"ui-icon\" viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"m10 7 5 5-5 5\"/></svg>"};
const bookmark = '<svg viewBox="0 0 18 22" aria-hidden="true"><path d="M3 2h12v18l-6-4-6 4Z"/></svg>';
const companyLogos = {microsoft:'microsoft.png',amazon:'amazon.png',aws:'aws.svg',goldman:'goldman.svg',walmart:'walmart.svg',tencent:'tencent.png',bytedance:'bytedance.png',alibaba:'alibaba.png',google:'google.png',nvidia:'nvidia-symbol.svg',apple:'apple.svg',cisco:'cisco.png',meta:'meta.svg',airwallex:'airwallex.png',shopee:'shopee.png',hsbc:'hsbc.png',standardchartered:'standardchartered.png',aia:'aia.svg',hkex:'hkex.png',hkjc:'hkjc-mark.svg',huawei:'huawei.png',oracle:'oracle.png',sap:'sap.svg',salesforce:'salesforce.svg',servicenow:'servicenow.svg',janestreet:'janestreet.svg'};
function mark(c) {const logo=companyLogos[c.id];return `<span class="company-mark official-logo ${esc(c.id)}">${logo?`<img src="logos/${logo}?v=16" alt="${esc(c.en)} Logo" width="48" height="32" decoding="async">`:`<span>${esc(c.name)}</span>`}</span>`;}
function toast(message) {$('#toast').textContent=message; $('#toast').hidden=false; clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,4500);}
function persistSaved() {try {localStorage.setItem('southbound:saved',JSON.stringify([...saved]));}catch {toast('浏览器未允许本地存储，收藏仅在本次打开期间保留。');}$('#saved-count').textContent=saved.size;}
async function toggleSave(id) {const had=saved.has(id);had?saved.delete(id):saved.add(id);persistSaved();render();syncDetailSave();
  if(!account)return;
  try{const list=await favoritesRequest(had?'remove':'add',{id});saved=new Set(list);persistSaved();render();syncDetailSave();}
  catch{had?saved.add(id):saved.delete(id);persistSaved();render();syncDetailSave();toast('收藏未能同步到账号，请稍后重试。');}}
async function favoritesRequest(action,extra={}) {
  const r=await fetch('/api/favorites',{method:'POST',headers:{'X-Requested-With':'SouthboundJobs','Content-Type':'application/json'},body:JSON.stringify({action,...extra})});
  if(!r.ok)throw new Error('favorites '+r.status);
  const data=await r.json();return Array.isArray(data.favorites)?data.favorites:[];}
function accountInitial() {const source=(account&&(account.name||account.email))||'';return source.trim().charAt(0).toUpperCase()||'S';}
function renderAccount() {
  const login=$('#login-link'),box=$('#account-user');
  if(!login||!box)return;
  login.hidden=!(googleEnabled&&!account);
  if(googleEnabled&&!account){const next=location.pathname+location.search+location.hash;login.href='/auth/google/start?next='+encodeURIComponent(next);}
  box.hidden=!account;
  if(account){const avatar=$('#account-avatar');
    if(avatar){if(account.picture){avatar.innerHTML='<img src="'+esc(account.picture)+'" alt="" referrerpolicy="no-referrer">';}else{avatar.textContent=accountInitial();}}
    const label=$('#account-name');if(label)label.textContent=account.name||account.email||'已登录';
    const mail=$('#account-email');if(mail)mail.textContent=account.email||'';}
  closeAccountMenu();}
function closeAccountMenu() {const menu=$('#account-menu'),button=$('#account-button');if(menu)menu.hidden=true;if(button)button.setAttribute('aria-expanded','false');}
async function loadAccount() {
  let info;
  try{const r=await fetch('/api/me',{headers:{'X-Requested-With':'SouthboundJobs'}});if(!r.ok)throw new Error('me '+r.status);info=await r.json();}
  catch{account=null;googleEnabled=false;renderAccount();return;}
  googleEnabled=!!info.googleEnabled;account=info.user||null;
  if(account){
    const remote=Array.isArray(info.favorites)?info.favorites:[];
    const local=[...saved];
    const missing=local.filter(id=>!remote.includes(id));
    let merged=remote;
    if(missing.length){try{merged=await favoritesRequest('merge',{ids:local});}catch{merged=remote.concat(missing);}}
    saved=new Set(merged);persistSaved();}
  renderAccount();render();}
async function signOut() {
  try{await fetch('/api/auth/logout',{method:'POST',headers:{'X-Requested-With':'SouthboundJobs'}});}catch{}
  account=null;renderAccount();render();toast('已退出登录，收藏仍保留在这台设备上。');}
function buildFilters() {
  $('#mobile-company').innerHTML='<option value="">全部公司</option>'+data.companies.filter(c=>c.source).map(c=>`<option value="${esc(c.id)}">${esc(c.name)}${c.id==='aws'?' AWS':''}</option>`).join('');
  $('#mobile-company').value=state.companies.size===1?[...state.companies][0]:'';
  $('#company-filters').innerHTML=data.companies.filter(c=>c.source).map(c=>`<label class="company-filter"><input type="checkbox" value="${esc(c.id)}" ${state.companies.has(c.id)?'checked':''}><span>${esc(c.name)}${c.id==='aws'?' AWS':''}</span><span class="number">${active().filter(j=>j.company===c.id&&(!state.city||j.cities.includes(state.city))).length}</span></label>`).join('');
}
function reset() {Object.assign(state,{city:'',companies:new Set(),category:'',type:'',query:'',quick:'all',limit:25});$('#search').value='';$('#city-select').value='';$('#category').value='';$('#role-type').value='';render();}
function filteredJobs() {
  const needle=state.query.trim().toLowerCase();
  return data.jobs.filter(j=>{
    const c=co(j.company);
    return (state.tab==='saved'?saved.has(j.id):j.status!=='missing') && (!state.city||j.cities.includes(state.city)) && (!state.companies.size||state.companies.has(j.company)) && (!state.category||j.category===state.category) && (!state.type||j.type===state.type) && (state.quick!=='new'||isNew(j)) && (state.quick!=='tech'||techCategories.has(j.category)) && (!needle||`${j.title} ${j.department} ${j.summary} ${j.category} ${j.cities.join(' ')} ${c.name} ${c.en}`.toLowerCase().includes(needle));
  }).sort((a,b)=>{
    if(state.sort==='tech'){const delta=Number(techCategories.has(b.category))-Number(techCategories.has(a.category));if(delta)return delta;}
    const aDate=state.sort==='seen'?a.firstSeenAt:a.postedAt, bDate=state.sort==='seen'?b.firstSeenAt:b.postedAt;
    return (bDate?Date.parse(bDate):0)-(aDate?Date.parse(aDate):0)||a.title.localeCompare(b.title);
  });
}
function jobCard(j) {
  const c=co(j.company), isSaved=saved.has(j.id);
  return `<article class="job-card">${mark(c)}<div class="job-body"><div class="job-company"><span>${esc(c.en)}</span><span class="verified">${j.status==='stale'?'待重新核验':j.status==='missing'?'未再检索到':'官方来源'}</span>${isNew(j)?'<span class="fresh">新收录</span>':''}</div><button class="job-title" data-detail="${esc(j.id)}" aria-haspopup="dialog" aria-label="查看职位详情：${esc(j.title)}">${esc(j.title)}</button><div class="job-meta"><span class="meta-location">${pin}${j.cities.map(esc).join(' / ')}</span><span class="meta-separator">·</span><span class="tag ${techCategories.has(j.category)?'tech':''}">${esc(j.category)}</span><span class="tag">${esc(j.type)}</span></div></div><div class="job-right"><div class="job-actions"><button class="save-button ${isSaved?'saved':''}" aria-label="${isSaved?'取消收藏':'收藏'} ${esc(j.title)}" aria-pressed="${isSaved}" data-save="${esc(j.id)}">${bookmark}</button><a class="apply-link" href="${safeUrl(j.url)}" target="_blank" rel="noopener noreferrer" aria-label="前往官网查看 ${esc(j.title)}">官网 ${icons.external}</a></div><time>${j.postedAt?formatDate(j.postedAt)+' 发布':'发布日期未提供'}</time></div></article>`;
}
function renderDirectory() {
  const needle=state.query.trim().toLowerCase();
  const companies=data.companies.filter(c=>(!state.companies.size||state.companies.has(c.id))&&(!needle||`${c.name} ${c.en} ${c.scope}`.toLowerCase().includes(needle)));
  $('#result-count').textContent=companies.length;
  $('#results-list').innerHTML='<div class="company-directory">'+companies.map(c=>{
    const count=active().filter(j=>j.company===c.id&&(!state.city||j.cities.includes(state.city))).length;
    const status=data.sources.find(s=>s.id===c.source);
    return `<article class="directory-card"><div class="directory-header">${mark(c)}<div><h3>${esc(c.name)}</h3><span class="en">${esc(c.en)}</span></div></div><span class="directory-badge ${!c.source?'manual':''}">${!c.source?'官方招聘入口':status?.status==='error'?'同步暂不可用':count+' 个已收录职位'}</span><p>${esc(c.scope)}</p><div class="directory-actions">${c.source?`<button data-company="${esc(c.id)}">查看职位 ${icons.chevron}</button>`:'<span>职位同步待接入</span>'}<a href="${safeUrl(c.url)}" target="_blank" rel="noopener noreferrer">招聘官网 ${icons.external}</a></div></article>`;
  }).join('')+'</div>';
  if(!companies.length)$('#results-list').innerHTML='<div class="empty"><strong>暂未找到匹配公司</strong><p>换个关键词试试。</p><button data-reset>清空筛选</button></div>';
  $('#load-more').hidden=true;
}
function render() {
  const jobs=active();
  $('#total-count').textContent=jobs.length.toLocaleString('en-US');$('#tech-count').textContent=jobs.filter(j=>techCategories.has(j.category)).length;
  $('#live-count').textContent=new Set(jobs.map(j=>j.company)).size;
  $('#edition-date').textContent=data.updatedAt?formatDate(data.updatedAt)+' · 招聘手记':'四城招聘手记';
  $('#last-update').textContent=data.updatedAt?'最近同步 '+formatDate(data.updatedAt,true)+(live?'':' · 快照'):'尚未同步';
  $('#saved-count').textContent=saved.size;
  $$('.city-count').forEach(el=>el.textContent=jobs.filter(j=>j.cities.includes(el.dataset.countCity)).length+' 个机会');
  $$('.city-card').forEach(el=>{el.classList.toggle('active',el.dataset.city===state.city);el.setAttribute('aria-pressed',String(el.dataset.city===state.city));});
  $$('.nav').forEach(el=>{el.classList.toggle('active',el.dataset.tab===state.tab);el.setAttribute('aria-pressed',String(el.dataset.tab===state.tab));});
  $$('.chip').forEach(el=>{el.classList.toggle('active',el.dataset.quick===state.quick);el.setAttribute('aria-pressed',String(el.dataset.quick===state.quick));});
  $('#city-select').value=state.city;
  $('#section-title').firstChild.textContent=state.tab==='saved'?'我的收藏 ':state.tab==='companies'?'公司目录 ':'发现职位 ';
  $('#section-eyebrow').textContent=state.tab==='saved'?'收藏感兴趣的下一站':state.tab==='companies'?'了解公司，发现适合你的团队':'为你汇集的官方职位';
  $('.quick-row').classList.toggle('hide',state.tab==='companies');$('#sort').classList.toggle('hide',state.tab==='companies');
  $('#category').disabled=state.tab==='companies';$('#role-type').disabled=state.tab==='companies';
  buildFilters();
  const failures=data.sources.filter(s=>s.status==='error');
  let note=state.tab==='companies'?'已同步企业与官方招聘入口分开标注；入口不计入职位数量。':state.tab==='saved'?(account?'收藏已保存到你的账号，换设备登录后自动同步。未再检索到的收藏仍会保留，请到官网核实。':'收藏保存在当前浏览器。登录后可跟着账号走。未再检索到的收藏仍会保留，请到官网核实。'):state.quick==='new'?'仅展示初次基线之后，今日首次发现的职位；不代表企业今日发布。':'点击职位名称，在弹窗内阅读完整 JD · 自动分类仅供筛选 · 各来源覆盖范围见「来源与更新」';
  if(failures.length)note+=' '+failures.map(s=>sourceNames[s.id]).join('、')+' 同步失败，已有记录保留待核验。';
  $('#data-note').textContent=note;$('#data-note').classList.toggle('warn',failures.length>0);
  if(state.tab==='companies'){renderDirectory();return;}
  const matched=filteredJobs();$('#result-count').textContent=matched.length;
  const rows=matched.slice(0,state.limit);
  if(!rows.length){$('#results-list').innerHTML=`<div class="empty"><strong>${state.tab==='saved'&&!saved.size?'把心动的机会留在这里。':'暂时没有匹配的机会。'}</strong><p>${state.tab==='saved'&&!saved.size?'点击职位右侧的书签即可收藏，之后在这里继续查看。':state.quick==='new'?'初次同步用于建立基线。之后发现的新职位会显示在这里。':'可以试试其他城市、公司或关键词，也可以到公司目录查看官网。'}</p><button data-reset>查看全部机会</button></div>`;}
  else if(state.group){const groups=new Map();rows.forEach(j=>{if(!groups.has(j.company))groups.set(j.company,[]);groups.get(j.company).push(j);});$('#results-list').innerHTML=[...groups].map(([id,items])=>`<h3 class="group-title">${esc(co(id).name)} <span>本页 ${items.length} 个职位</span></h3>${items.map(jobCard).join('')}`).join('');}
  else $('#results-list').innerHTML=rows.map(jobCard).join('');
  $('#load-more').hidden=matched.length<=state.limit;$('#load-more').textContent=`加载更多职位 · 还有 ${Math.max(0,matched.length-state.limit)} 个`;
}
function setTab(tab) {state.tab=tab;state.limit=25;location.hash=tab;render();}
let detailId = '', detailSequence = 0, activeDetail = null;
const detailCache = new Map();
function syncDetailSave() {
  const button = $('#detail-save');
  if(button){button.textContent=saved.has(detailId)?'已收藏 · 取消收藏':'收藏职位';button.setAttribute('aria-pressed',String(saved.has(detailId)));}
}
function showDetail(id) {
  const j=data.jobs.find(x=>x.id===id);if(!j)return;
  detailId=id;
  activeDetail=null;
  const c=co(j.company);
  const entries=[['部门 / 方向',j.department||j.category],['工作地点',j.locations.join(' / ')],['官网发布日期',j.postedAt?formatDate(j.postedAt):'未提供'],['首次收录',formatDate(j.firstSeenAt,true)+(j.baseline?' · 初始基线':'')],['最近核验',formatDate(j.lastSeenAt,true)+'（UTC+8）'],['数据状态',j.status==='stale'?'来源暂不可用，待重新核验':j.status==='missing'?'最近同步未再检索到':'最近同步可检索'],['来源范围',c.scope]];
  $('#detail-content').innerHTML=`<div class="detail-header">${mark(c)}<div><strong>${esc(c.name)}</strong><p>${esc(c.en)}</p></div></div><h2 id="detail-title">${esc(j.title)}</h2><div class="detail-meta"><span class="tag">${esc(j.category)}</span><span class="tag">${esc(j.type)}</span>${j.cities.map(city=>`<span class="tag">${esc(city)}</span>`).join('')}</div><dl class="detail-table">${entries.map(([name,value])=>`<dt>${esc(name)}</dt><dd>${esc(value)}</dd>`).join('')}</dl><section class="jd-section" aria-labelledby="jd-heading"><h3 id="jd-heading">完整职位描述 <span>JOB DESCRIPTION</span></h3><p class="jd-origin">企业官网原文 · 保留原始语言</p><div id="jd-content" aria-live="polite" aria-busy="true"></div></section><div class="detail-actions"><a class="primary-link" href="${safeUrl(j.url)}" target="_blank" rel="noopener noreferrer">前往官网申请 ${icons.external}</a><button id="detail-save" class="secondary-button" data-save="${esc(j.id)}"></button></div>`;
  syncDetailSave();
  const dialog=$('#detail-dialog');if(!dialog.open)dialog.showModal();dialog.scrollTop=0;$('#detail-content').scrollTop=0;
  loadDetail(id);
}
async function loadDetail(id) {
  const sequence=++detailSequence;
  const j=data.jobs.find(x=>x.id===id);if(!j)return;
  const area=$('#jd-content');area.setAttribute('aria-busy','true');area.innerHTML='<p class="jd-loading">正在读取官方完整职位描述…</p>';
  try {
    let detail=detailCache.get(id);
    if(!detail){
      const local=live;
      if(!local&&!j.detailPath)throw new Error();
      const response=await fetch(local?'/api/job-detail?id='+encodeURIComponent(id):j.detailPath,{cache:'no-store'});
      if(!response.ok)throw new Error();detail=await response.json();
      if(detail.id!==id||!Array.isArray(detail.sections)||!detail.sections.length)throw new Error();
      if(!detail.stale)detailCache.set(id,detail);
    }
    if(sequence!==detailSequence||detailId!==id||!$('#detail-dialog').open)return;
    area.innerHTML=(detail.stale?'<p class="jd-warning">官网正文暂时无法更新，以下为上次保存的内容。</p>':'')+detail.sections.map(s=>`<section class="jd-block">${s.heading?`<h4>${esc(s.heading)}</h4>`:''}<div class="jd-text">${esc(s.text)}</div></section>`).join('')+`<p class="jd-fetched">正文获取于 ${esc(formatDate(detail.fetchedAt,true))}（UTC+8）</p>`;
    activeDetail=detail;
    if(detail.translationEligible){
      area.insertAdjacentHTML('beforeend','<section id="jd-translation" class="jd-translation" aria-labelledby="translation-heading"><div class="translation-heading-row"><h3 id="translation-heading">AI 中文翻译 <span>简体中文</span></h3><button class="text-button" data-back-original>返回英文原文</button></div><p class="translation-note">与英文原文逐段对应 · AI 翻译仅供参考，具体要求以原文为准</p><div id="translation-content" aria-live="polite" aria-busy="true"></div></section>');
      area.insertAdjacentHTML('afterbegin','<button class="translation-jump" data-scroll-translation>阅读 AI 中文翻译</button>');
      loadTranslation(detail,sequence);
    }
  } catch {
    if(sequence!==detailSequence||detailId!==id||!$('#detail-dialog').open)return;
    area.innerHTML=`<div class="jd-error"><p>${location.protocol==='file:'?'请通过本地网站打开，以读取完整 JD。':'暂时无法读取完整 JD。请重试，或使用下方官网按钮查看。'}</p><button class="secondary-button" data-detail-retry="${esc(id)}">重新加载正文</button></div>`;
  } finally {if(sequence===detailSequence)area.setAttribute('aria-busy','false');}
}
async function loadTranslation(detail,sequence) {
  const current=()=>sequence===detailSequence&&detailId===detail.id&&$('#detail-dialog').open;
  if(!current())return;
  const area=$('#translation-content');if(!area)return;
  area.setAttribute('aria-busy','true');area.innerHTML='<p class="translation-loading"><span class="translation-dot" aria-hidden="true"></span>正在准备中文译文…你可以先阅读英文原文。</p>';
  const retry=message=>{area.innerHTML=`<div class="translation-unavailable"><p>${esc(message)}</p>${live?`<button class="secondary-button" data-translation-retry="${esc(detail.id)}">重试中文翻译</button>`:''}</div>`;};
  try {
    let result;
    if(!live){
      const response=await fetch(detail.translationPath,{cache:'no-store'});
      if(!response.ok){if(current())retry('此职位尚未生成中文译文。请在本地网站中打开并生成，再发布更新。');return;}
      result={status:'ready',translation:await response.json()};
    }else{
      const url='/api/job-translation?id='+encodeURIComponent(detail.id);
      const response=await fetch(url,{cache:'no-store'});if(!response.ok)throw new Error();result=await response.json();
      if(!current())return;
      if(['available','error'].includes(result.status)){
        const start=await fetch('/api/translate?id='+encodeURIComponent(detail.id),{method:'POST',headers:{'X-Requested-With':'SouthboundJobs'}});
        if(!start.ok)throw new Error();result=await start.json();
      }
      const began=Date.now();
      while(result.status==='pending'){
        if(!current())return;
        area.innerHTML='<p class="translation-loading"><span class="translation-dot" aria-hidden="true"></span>AI 正在逐段翻译，首次生成可能需要一些时间…</p>';
        await new Promise(resolve=>setTimeout(resolve,2000));if(!current())return;
        const poll=await fetch(url,{cache:'no-store'});if(!poll.ok)throw new Error();result=await poll.json();
        if(Date.now()-began>6*60*1000){retry('翻译仍在后台进行，稍后点击重试即可查看结果。');return;}
      }
    }
    if(!current())return;
    if(result.status==='limited'){area.innerHTML='<p class="translation-unavailable">今日翻译额度已用完，请明天再试。已有译文和英文原文仍可阅读。</p>';return;}
    if(result.status==='unconfigured'){area.innerHTML='<p class="translation-unavailable">中文翻译服务尚未启用，英文原文可正常阅读。</p>';return;}
    if(result.status!=='ready'){retry(result.status==='busy'?'翻译队列较忙，请稍后重试。':'中文翻译暂时未完成，请重试。英文原文可正常阅读。');return;}
    const t=result.translation;
    if(t.id!==detail.id||t.sourceHash!==detail.sourceHash||!Array.isArray(t.sections)||t.sections.length!==detail.sections.length)throw new Error();
    if(!t.sections.every((s,i)=>typeof s.text==='string'&&s.text.split('\n').length===detail.sections[i].text.split('\n').length))throw new Error();
    area.innerHTML=t.sections.map(s=>`<section class="jd-block zh-block">${s.heading?`<h4>${esc(s.heading)}</h4>`:''}<div class="jd-text" lang="zh-CN">${esc(s.text)}</div></section>`).join('')+`<p class="jd-fetched">AI 译文生成于 ${esc(formatDate(t.translatedAt,true))}（UTC+8）</p>`;
  }catch{if(current())retry('暂时无法生成中文译文，请稍后重试。英文原文可正常阅读。');}
  finally{if(current())area.setAttribute('aria-busy','false');}
}
$('#detail-dialog').addEventListener('close',()=>{detailSequence++;});
function updates() {
  $('#schedule-note').textContent=serverMode?'每天 09:00（北京时间 / 新加坡时间）由服务器自动同步。管理员可在服务器发起手动同步。':live?'本地服务运行期间，每天 09:00（北京时间 / 新加坡时间）同步。错过后将在启动时补跑；也可以点击右上角立即更新。电脑关闭时不会运行。':'当前为静态快照。若部署至 GitHub Pages，配套工作流每天 09:00（UTC+8）运行，也可手动触发。仅打开本地 HTML 不会自动抓取新职位。';
  $('#source-status').innerHTML=Object.entries(sourceNames).map(([key,name])=>{const s=data.sources.find(s=>s.id===key);return `<div class="source-row"><div>${esc(name)}<small>${s?.lastSuccessAt?'最近成功 '+formatDate(s.lastSuccessAt,true):'尚未成功同步'}${s?' · '+s.count+' 个职位':''}</small>${s?.message?`<small>${esc(s.message)}</small>`:''}${key==='walmart'?'<small>中国支持中心招聘，不含门店及港新岗位</small>':''}</div><span class="${s?.status==='ok'?'status-ok':'status-error'}">${s?.status==='ok'?'● 已同步':'○ 待核验'}</span></div>`;}).join('');
  $('#history').innerHTML=data.history.slice(0,10).map(h=>`<div class="history-row"><span>${esc(formatDate(h.at,true))}</span><span>${h.ok}/${h.total} 来源成功 · ${h.baseline?'建立基线 '+h.added+' 个':'新增收录 '+h.added+' 个'} · ${h.missing} 个未再检索到</span></div>`).join('')||'<p>还没有同步记录。</p>';
  $('#updates-dialog').showModal();
}
async function load() {
  if(window.JOBS_SNAPSHOT){data=window.JOBS_SNAPSHOT;render();}
  if(location.protocol==='http:'||location.protocol==='https:'){
    try {const response=await fetch('/api/jobs',{cache:'no-store'});if(!response.ok)throw new Error();const fresh=await response.json();if(!Array.isArray(fresh.jobs))throw new Error();data=fresh;live=true;render();const status=await fetch('/api/status').then(r=>r.json());serverMode=status.mode==='server';setRefreshing(false);if(status.running)watchRefresh();}
    catch {if(!window.JOBS_SNAPSHOT){$('#results-list').innerHTML='<div class="empty"><strong>暂时无法读取职位</strong><p>请运行 python server.py 后重新打开网站。</p></div>';}}
  }
}
function setRefreshing(on) {refreshing=on;$('#refresh').disabled=on&&!serverMode;$('#refresh').classList.toggle('busy',on);$('#refresh').innerHTML=`${icons.refresh} ${on?'正在同步…':serverMode?'更新记录':'更新职位'}`;$('#refresh').setAttribute('aria-label',serverMode?'查看更新记录':'更新职位');}
async function watchRefresh() {
  if(refreshing)return;setRefreshing(true);const began=Date.now();
  try{
    while(true){await new Promise(r=>setTimeout(r,2500));const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw new Error();const s=await r.json();if(!s.running)break;if(Date.now()-began>20*60*1000)throw new Error();}
    const r=await fetch('/api/jobs',{cache:'no-store'});if(!r.ok)throw new Error();data=await r.json();detailCache.clear();render();const failed=data.sources.filter(s=>s.status==='error');toast(failed.length?`同步完成，${failed.length} 个来源需稍后重试。详见更新记录。`:'职位已更新，新的可能已就位。');
  }catch{toast('暂时无法确认同步结果，请稍后重新加载页面。');}finally{setRefreshing(false);}
}
$('#refresh').addEventListener('click',async()=>{
  if(serverMode){updates();return;}
  if(!live){toast('当前为静态快照：请启动本地服务，或在 GitHub Actions 手动运行更新工作流。');updates();return;}
  $('#refresh').disabled=true;
  try{const r=await fetch('/api/refresh',{method:'POST',headers:{'X-Requested-With':'SouthboundJobs'}});if(!r.ok)throw new Error();toast('正在核验官方职位，完整 JD 同步可能需要几分钟。');watchRefresh();}catch{$('#refresh').disabled=false;toast('更新未能启动，请检查本地服务。');}
});
document.addEventListener('click',event=>{const el=event.target.closest('button,a');if(!el)return;
  if(el.dataset.tab)setTab(el.dataset.tab);
  if(el.dataset.city){state.city=state.city===el.dataset.city?'':el.dataset.city;state.limit=25;render();}
  if(el.dataset.quick){state.quick=el.dataset.quick;state.limit=25;render();}
  if(el.dataset.save)toggleSave(el.dataset.save);
  if(el.dataset.detail)showDetail(el.dataset.detail);
  if(el.dataset.detailRetry)loadDetail(el.dataset.detailRetry);
  if(el.dataset.translationRetry&&activeDetail?.id===el.dataset.translationRetry)loadTranslation(activeDetail,detailSequence);
  if(el.hasAttribute('data-scroll-translation'))$('#jd-translation')?.scrollIntoView({behavior:'smooth',block:'start'});
  if(el.hasAttribute('data-back-original'))$('#jd-heading')?.scrollIntoView({behavior:'smooth',block:'start'});
  if(el.dataset.company){state.companies=new Set([el.dataset.company]);state.category='';state.type='';state.query='';state.quick='all';$('#category').value='';$('#role-type').value='';$('#search').value='';setTab('jobs');}
  if(el.hasAttribute('data-reset')){state.tab='jobs';reset();}
});
$('#company-filters').addEventListener('change',event=>{const {value,checked}=event.target;checked?state.companies.add(value):state.companies.delete(value);state.limit=25;render();});
$('#mobile-company').addEventListener('change',e=>{state.companies=new Set(e.target.value?[e.target.value]:[]);state.limit=25;render();});
$('#search').addEventListener('input',event=>{state.query=event.target.value;state.limit=25;render();});
for(const [id,key] of [['city-select','city'],['category','category'],['role-type','type'],['sort','sort']])$('#'+id).addEventListener('change',event=>{state[key]=event.target.value;state.limit=25;render();});
$('#group-company').addEventListener('change',e=>{state.group=e.target.checked;render();});
$('#load-more').addEventListener('click',()=>{state.limit+=25;render();});
$('#reset').addEventListener('click',reset);
['open-updates','source-info','footer-updates'].forEach(id=>$('#'+id).addEventListener('click',updates));
$$('.dialog-close').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
$$('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();}}));
document.addEventListener('keydown',e=>{if(e.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&!$('dialog[open]')){e.preventDefault();$('#search').focus();}});
$('#account-button')?.addEventListener('click',e=>{e.stopPropagation();const menu=$('#account-menu');if(!menu)return;const open=menu.hidden;menu.hidden=!open;$('#account-button').setAttribute('aria-expanded',String(open));});
$('#logout')?.addEventListener('click',()=>{closeAccountMenu();signOut();});
document.addEventListener('click',e=>{if(!e.target.closest('.account'))closeAccountMenu();});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeAccountMenu();});
window.addEventListener('hashchange',()=>{if(['jobs','saved','companies'].includes(location.hash.slice(1))){state.tab=location.hash.slice(1);render();}});
if(['jobs','saved','companies'].includes(location.hash.slice(1)))state.tab=location.hash.slice(1);
load();
loadAccount();
// Keep an already-open local dashboard current after the daily server-side job.
setInterval(async()=>{
  if(!live||refreshing||document.hidden)return;
  try{
    const status=await fetch('/api/status',{cache:'no-store'}).then(r=>r.json());
    if(status.running){watchRefresh();return;}
    const fresh=await fetch('/api/jobs',{cache:'no-store'}).then(r=>r.json());
    if(Array.isArray(fresh.jobs)&&fresh.updatedAt!==data.updatedAt){detailCache.clear();data=fresh;render();}
  }catch{/* Preserve the last usable snapshot during a temporary connection loss. */}
},60000);
