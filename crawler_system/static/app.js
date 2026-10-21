'use strict';
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const typeName = {web:'Web',paper:'Paper',pdf:'PDF',video:'Video',audio:'Audio',feed:'Feed',text:'Text',file:'File',playlist:'Playlist'};
const regionName = {all:'All',domestic:'Domestic',foreign:'International',unknown:'Uncategorized'};
const mediaKinds = new Set(['media','video','audio','mp4','webm','mov','mp3','wav','m4a','ogg']);
const stateName = {queued:'Queued',running:'Running',completed:'Completed',partial:'Partial',failed:'Failed',cancelled:'Cancelled',interrupted:'Interrupted'};
const textName = {resources:'Identified Resources',full_text:'Full Text Extracted',listing:'Content List Extracted',abstract:'Abstract',metadata_only:'Metadata Only',search_snippet:'Search Snippet',subtitles:'Subtitles Available',needs_ocr:'Needs OCR',partial:'Partial Content',empty:'No Full Text',available:'Saved'};
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.get('token')) { sessionStorage.setItem('crawler-token', fragment.get('token')); history.replaceState(null,'',location.pathname); }
let secret = sessionStorage.getItem('crawler-token') || '';
let selectedDownloads = new Set();
let selectedMode = 'auto', selectedId = '', detail = null, detailTab = 'content', currentView = 'library', busy = false, searchTimer, toastTimer;

async function api(path, options = {}) {
  const response = await fetch('/api' + path, {...options, headers:{'Authorization':'Bearer ' + secret, 'Content-Type':'application/json', ...(options.headers || {})}});
  if (!response.ok) {
    let message = response.status === 401 ? 'Please launch via the desktop shortcut to connect to local library' : 'Operation failed';
    try { const data = await response.json(); const value = data.detail; message = typeof value === 'string' ? value : Array.isArray(value) ? value.map(row => row.msg).join('; ') : value?.message || message; } catch {}
    throw new Error(message);
  }
  return response;
}
async function json(path, options = {}) { return (await api(path,options)).json(); }
function toast(message, error = false) { $('toast').textContent = message; $('toast').className = 'toast' + (error ? ' error' : ''); $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, error ? 7000 : 3500); }
function hostname(url) { try { return new URL(url).hostname.replace(/^www\./,''); } catch { return url; } }
function date(value) { return value ? new Date(value).toLocaleString('en-US',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : ''; }
function bytes(size) { return size >= 1048576 ? (size / 1048576).toFixed(1) + ' MB' : Math.round(size / 1024) + ' KB'; }
function empty(title, description) { return `<div class="empty-state"><span class="empty-symbol">▤</span><strong>${esc(title)}</strong><p>${esc(description)}</p></div>`; }

async function loadLibrary() {
  const query = $('library-search').value, kind = $('kind-filter').value, region = $('region-filter').value;
  const data = await json('/items?' + new URLSearchParams({q:query,kind,region,limit:100}));
  $('item-count').textContent = `${regionName[region]} · ${data.total} item(s)${data.total > data.items.length ? ` · Displaying ${data.items.length}` : ''}`;
  for (const option of $('region-filter').options) { if (option.value !== 'all') option.textContent = `${regionName[option.value]}${option.value === 'unknown' ? ' Items' : ' Library'} (${data.region_counts[option.value] || 0})`; }
  $('library-list').innerHTML = data.items.length ? data.items.map(item => `<button class="item-row${selectedId === item.id ? ' selected' : ''}" data-item="${esc(item.id)}"><span class="item-icon">${esc(typeName[item.kind] || 'Item')}</span><span class="item-text"><span class="item-title">${esc(item.title)}</span><span class="item-meta">${esc(regionName[item.source_region] || 'Uncategorized')} · ${esc(hostname(item.url))} · ${esc(textName[item.text_status] || item.text_status)} · ${esc(date(item.updated_at))}</span><span class="item-preview">${esc(item.preview || (item.warnings || []).join('; ') || 'Source and metadata saved, click to view details')}</span></span><span class="item-arrow">↗</span></button>`).join('') : empty('No matching items in current library','Switch source partition or clear filters; use the input box above to search the web.');
  $('library-list').querySelectorAll('[data-item]').forEach(button => button.onclick = () => openItem(button.dataset.item));
}

async function openItem(id) {
  if (selectedId !== id) selectedDownloads.clear();
  selectedId = id;
  try { const item = await json('/items/' + id); if (selectedId !== id) return; detail = item; detailTab = item.entries?.length ? 'entries' : item.assets?.some(asset=>mediaKinds.has(asset.kind)) ? 'media' : 'assets'; renderDetail(); $('inspector').hidden = false; await loadLibrary(); } catch (error) { toast(error.message,true); }
}
function renderDetail() {
  if (!detail) return;
  const item = detail;
  const videoList = item.entries?.some(entry=>entry.kind === 'video');
  const mediaCount = (item.assets || []).filter(asset=>mediaKinds.has(asset.kind)).length;
  const otherCount = (item.assets || []).length - mediaCount;
  const tabs = [...(item.entries?.length ? [['entries',(videoList ? 'Video List ' : 'Content List ') + item.entries.length]] : []), ...(mediaCount ? [['media','Video / Audio ' + mediaCount]] : []), ['assets',(videoList || mediaCount ? 'Covers & Attachments ' : 'Attachments ') + otherCount]];
  $('inspector-content').innerHTML = `<div class="detail-heading"><span class="state-label">${esc(typeName[item.kind] || 'Item')}</span><h2>${esc(item.title)}</h2><div class="detail-meta">${esc((item.authors || []).filter(Boolean).join(', '))}${item.published ? '<br>' + esc(item.published) : ''}<br>${esc(textName[item.text_status] || item.text_status)}${item.metadata?.pages ? ' · ' + item.metadata.pages + ' pages' : ''}${item.metadata?.duration ? ' · ' + Math.round(item.metadata.duration) + ' sec' : ''}<br>Extraction method: ${esc(item.extractor === 'scrapling-browser' ? 'Browser Dynamic Rendering' : item.extractor === 'scrapling-static' ? 'Static Webpage' : item.extractor)}<br>Fetched at: ${esc(date(item.fetched_at))}</div><a class="source-link" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">${esc(item.url)} ↗</a><div class="detail-actions"><button class="secondary" id="detail-export">Export Item</button><button class="secondary" id="recollect">Recollect</button></div></div>${item.warnings?.length ? '<div class="warning-text">' + item.warnings.map(esc).join('<br>') + '</div>' : ''}<div class="detail-tabs">${tabs.map(([key,label]) => `<button class="${detailTab === key ? 'active' : ''}" data-tab="${key}">${esc(label)}</button>`).join('')}</div><div class="detail-body" id="detail-body"></div>`;
  $('inspector-content').querySelectorAll('[data-tab]').forEach(button => button.onclick = () => {detailTab = button.dataset.tab; renderDetail();});
  $('detail-export').onclick = () => exportLibrary([item.id]);
  const sourceControls = document.createElement('div');
  sourceControls.className = 'source-classification';
  sourceControls.innerHTML = `<label for="detail-region">Source Classification</label><select id="detail-region">${['domestic','foreign','unknown','auto'].map(value=>`<option value="${value}"${value === item.source_region ? ' selected' : ''}>${value === 'auto' ? 'Restore Auto Classification' : regionName[value]}</option>`).join('')}</select><button id="save-source-region" class="secondary">Set For This Site</button><small>Classified by ${esc(hostname(item.url))}, independent of content language.</small>${item.material_path ? `<small>Material file: ${esc(item.material_path)}</small>` : '<small>Legacy items saved in shared index; material file will be created upon recollection.</small>'}`;
  $('inspector-content').querySelector('.detail-heading').append(sourceControls);
  $('save-source-region').onclick = async()=>{try {await json('/source-region',{method:'PUT',body:JSON.stringify({url:item.url,region:$('detail-region').value})});toast('Source site classification updated');await openItem(item.id);}catch(error){toast(error.message,true);}};
  $('recollect').onclick = async () => { try { await json('/jobs',{method:'POST',body:JSON.stringify({inputs:[item.url],mode:item.kind === 'video' || item.kind === 'audio' ? 'video' : 'auto'})}); toast('Collection task resubmitted'); await refresh(); } catch(error) {toast(error.message,true);} };
  const rows = detailTab === 'entries'
    ? (item.entries || []).map(entry=>({entry,index:item.entries.indexOf(entry)}))
    : (item.assets || []).map((asset,index)=>({asset,index})).filter(({asset})=>detailTab === 'media' ? mediaKinds.has(asset.kind) : !mediaKinds.has(asset.kind));
  $('detail-body').innerHTML = `<div class="download-limit-label">Per-file limit <input type="number" id="asset-limit" min="0" value="${esc($('download-limit').value || 0)}" aria-label="Attachment download limit MB"> MB (0 for unlimited)</div><div class="detail-actions"><button class="secondary" id="select-all-resources">Select All</button><button class="secondary" id="clear-resources">Clear Selection</button><button class="primary" id="download-selected">Download Selected (${selectedDownloads.size})</button></div>` + (rows.length ? rows.map(({entry,asset,index})=>{
    const value=entry||asset, key=(entry?'e':'a')+index, usable=!entry || ['video','audio','web','paper','pdf','file'].includes(entry.kind);
    return `<div class="asset-row"><label><input type="checkbox" data-resource="${key}" ${selectedDownloads.has(key)?'checked':''} ${usable?'':'disabled'} aria-label="Select ${esc(value.title || value.name || value.url)}"><a href="${esc(value.source_url || value.url)}" target="_blank" rel="noopener noreferrer">${esc(value.title || value.name || value.url)}</a></label><small>${esc(entry?entry.kind:asset.kind)} · ${entry?.kind==='program'?'Program entry, please open specific video first':item.metadata?.media_url_status==='not_extracted'&&detailTab==='media'?'Video entry, click to parse original file':'Download on selection'}</small>${usable?`<button class="secondary" data-download-one="${key}">Download Item</button>`:''}</div>`;
  }).join('') : '<p>No resources found in this category yet; dynamic pages may still be updating.</p>');
  const selectors=Array.from($('detail-body').querySelectorAll('[data-resource]:not(:disabled)'));
  const updateCount=()=>{$('download-selected').textContent=`Download Selected (${selectedDownloads.size})`;};
  selectors.forEach(box=>box.onchange=()=>{box.checked?selectedDownloads.add(box.dataset.resource):selectedDownloads.delete(box.dataset.resource);updateCount();});
  $('select-all-resources').onclick=()=>{selectors.forEach(box=>{box.checked=true;selectedDownloads.add(box.dataset.resource);});updateCount();};
  $('clear-resources').onclick=()=>{selectedDownloads.clear();selectors.forEach(box=>box.checked=false);updateCount();};
  const downloadKeys=async(keys)=>{
    if(!keys.length){toast('Please select resources to download first');return;}
    const max_mb=Number($('asset-limit').value||0);
    const downloads=keys.map(key=>({item_id:item.id,max_mb,...key.startsWith('e')?{entry_index:Number(key.slice(1))}:{asset_index:Number(key.slice(1))}}));
    try{const result=await json('/downloads/batch',{method:'POST',body:JSON.stringify({downloads})});toast(`Submitted ${result.queued} download task(s)`);await refresh();}catch(error){toast(error.message,true);}
  };
  $('download-selected').onclick=()=>downloadKeys([...selectedDownloads]);
  $('detail-body').querySelectorAll('[data-download-one]').forEach(button=>button.onclick=()=>downloadKeys([button.dataset.downloadOne]));
}

async function loadJobs() {
  const {jobs} = await json('/jobs');
  const active = jobs.filter(job => ['queued','running'].includes(job.state));
  $('active-count').textContent = active.length || '';
  $('live-task').hidden = active.length === 0;
  if (active.length) { const job = active[0]; $('live-task').innerHTML = `<span class="spinner"></span><span>${esc(job.message || 'Task queued')} ${active.length > 1 ? ` · Another ${active.length-1} task(s)` : ''}</span><button id="show-tasks">View Progress →</button>`; $('show-tasks').onclick=()=>switchView('jobs'); }
  const expanded = new Set(Array.from($('job-list').querySelectorAll('details[data-job][open]'), row => row.dataset.job));
  $('job-list').innerHTML = jobs.length ? jobs.map(job => `<div class="job-row"><div class="job-title"><strong>${esc((job.request.inputs || []).join(' · ').slice(0,250))}</strong><span class="state-label ${esc(job.state)}">${esc(stateName[job.state] || job.state)}</span></div><div class="job-meta">${esc(date(job.created_at))} · ${job.progress}/${job.total} item(s) · ${job.item_ids.length} library item(s)${job.downloads.length ? ' · ' + job.downloads.length + ' download(s)' : ''} · ${esc(regionName[job.request.source_region || job.request.region || 'all'])} Source</div><div class="job-message">${esc(job.message || '')}${job.request.storage_root ? '<br>Storage location: ' + esc(job.request.storage_root) : ''}${(job.events || []).filter(event=>event.scope).map(event=>'<br>Scope filter: matched ' + event.scope.matched + ', excluded ' + event.scope.excluded + ' from other regions, unconfirmed ' + event.scope.unknown).join('')}</div>${discoveryDetails(job.events || [])}${job.errors.length ? '<details class="error-list" data-job="' + esc(job.id) + '"' + (expanded.has(job.id) ? ' open' : '') + '><summary>View incomplete items and causes (' + job.errors.length + ')</summary>' + job.errors.map(error => `${esc(error.channel || error.input || '')}<br>${esc(error.message)}<br>${esc(error.action || '')}`).join('<br><br>') + '</details>' : ''}<div class="job-actions">${job.item_ids.length ? `<button class="secondary" data-result="${esc(job.item_ids[0])}">${job.readable_items > 0 ? 'Read Extracted Content' : 'View Discovered Items'}</button>` : ''}${['queued','running'].includes(job.state) ? `<button class="secondary" data-cancel="${esc(job.id)}">Cancel Task</button>` : `<button class="secondary" data-retry="${esc(job.id)}">Retry Task</button>`}</div>${job.downloads.map(record => `<div class="download-record">${esc(record.filename)} · ${esc(bytes(record.bytes))}<br>${esc(record.path)}<button data-file="${esc(record.id)}">Save File As</button><br>SHA-256: ${esc(record.sha256)}</div>`).join('')}</div>`).join('') : empty('No tasks yet','Start by entering a URL or keywords on the collector workbench.');
  $('job-list').querySelectorAll('[data-result]').forEach(button => button.onclick=()=>openItem(button.dataset.result));
  $('job-list').querySelectorAll('[data-cancel]').forEach(button => button.onclick=async()=>{await json('/jobs/' + button.dataset.cancel + '/cancel',{method:'POST'});toast('Task cancelled');await refresh();});
  $('job-list').querySelectorAll('[data-retry]').forEach(button => button.onclick=async()=>{try {const job=jobs.find(value=>value.id===button.dataset.retry); if(job.request.operation==='download'){toast('Please initiate download again from item attachments');return;} await json('/jobs',{method:'POST',body:JSON.stringify(job.request)});toast('Task resubmitted');await refresh();}catch(error){toast(error.message,true);}});
  $('job-list').querySelectorAll('[data-file]').forEach(button => button.onclick=async()=>{try{const record=jobs.flatMap(job=>job.downloads).find(row=>row.id===button.dataset.file);await saveResponse(await api('/downloads/' + record.id + '/file'),record.filename);}catch(error){toast(error.message,true);}});
}

function discoveryDetails(events) {
  return events.filter(event=>event.discovery).map(event=>{
    const discovery=event.discovery;
    return `<details class="error-list"><summary>Broad Search Plans & Channels · ${discovery.requests} searches · ${discovery.not_run} unexecuted plans</summary><p>Keywords: ${esc(discovery.variants.map(row=>row.query).join('; '))}</p>${(discovery.channels||[]).map(row=>`<p>${esc(row.channel)} · ${esc({completed:'Results Obtained',empty:'No Results',partial:'Partial',failed:'Incomplete',not_run:'Not Executed (Budget)'}[row.state]||row.state)} · ${row.found||0} found${row.message?'<br>'+esc(row.message):''}</p>`).join('')}<p>${esc(discovery.note)}</p></details>`;
  }).join('');
}
async function loadTools() {
  const data = await json('/status');
  const names = {'scrapling':'Web Scraping','yt-dlp':'Video Downloader','mcp':'MCP Interface'};
  $('tool-list').innerHTML = data.tools.map(tool=>`<div class="tool-row"><div><strong>${esc(names[tool.name] || tool.name)}</strong><small>${esc(tool.description)} · ${esc(tool.version || 'Not Installed')}</small></div><span class="state-label">${tool.available ? 'Installed' : 'Not Installed'}</span></div>`).join('') + `<div class="tool-row"><div><strong>Browser Rendering</strong><small>Directly controls Edge for dynamic resources, no extension required</small></div><span class="state-label">${data.browser_configured ? 'Cache Configured' : 'Configuration Needed'}</span></div><div class="tool-row"><div><strong>Dedicated Login Profile</strong><small>Uses system Edge profile directory</small></div><span class="state-label">${data.browser_profile_configured ? 'Created' : 'Created on First Use'}</span></div><div class="tool-row"><div><strong>Extractors in Library</strong><small>${esc(data.recent_extractors.join(', ') || 'No collection records yet')}</small></div></div>`;
  $('browser-path').value=data.settings.browser_cache;$('browser-profile').value=data.settings.browser_profile;$('domain-interval').value=data.settings.domain_interval;$('download-limit').value=data.settings.max_download_mb;$('platform-note').textContent=data.platform_note;
  $('storage-path').value=data.storage.root;
  $('storage-current').textContent='Current storage location: ' + data.storage.root + ' (Domestic / International / Uncategorized subdirectories)';
  $('index-path').textContent='Shared index: ' + data.storage.index;
  await loadPlatformCatalog(true);
}

async function loadPlatformCatalog(showDetails=false) {
  const data=await json('/platforms');
  const selected=$('search-platform').value;
  const rows=data.platforms.filter(row=>$('search-region').value==='all'||row.region===$('search-region').value);
  $('search-platform').innerHTML='<option value="">All Major Platforms + Other Public Sites</option>'+rows.map(row=>`<option value="${esc(row.id)}">${esc(regionName[row.region])} · ${esc(row.name)}</option>`).join('');
  if(rows.some(row=>row.id===selected)) $('search-platform').value=selected;
  if(showDetails) {
    const categories={news:'News',social:'Social',video:'Video',audio:'Audio / Podcast',paper:'Paper',tech:'Tech',reference:'Reference',commerce:'E-Commerce'};
    $('tool-list').insertAdjacentHTML('beforeend',`<div class="platform-directory"><h3>Major Platform Directory · ${data.platforms.length}</h3><p>${esc(data.note)}</p><details><summary>View Domestic & International Platforms, Prefixes, and Recent Checks</summary>${data.platforms.map(row=>{const checks=(data.recent_checks.checks||[]).filter(check=>check.platform===row.id);const last=checks.at(-1);return `<div class="tool-row"><div><strong>${esc(row.name)} <small>${esc(regionName[row.region])} · ${esc(categories[row.category])}</small></strong><small>Keyword prefix: ${esc(row.id)}: · ${esc(row.domains.join(', '))}</small><small>${last?esc(last.stage+': '+(last.state==='completed'?'Verified Pass':last.code||last.state)+' · '+(last.checked_at||'Check time unrecorded')):'Public collection and download not verified per site'}</small></div><span class="state-label">${row.native_prefix?'Dedicated Search Entry':'Public Index Entry'}</span></div>`;}).join('')}</details></div>`);
  }
}
async function switchView(view) {
  currentView=view;
  document.querySelectorAll('.view').forEach(section=>section.hidden=section.id!==view+'-view');
  document.querySelectorAll('[data-view]').forEach(button=>button.classList.toggle('active',button.dataset.view===view));
  $('page-label').textContent={library:'Collector Workbench',jobs:'Tasks',tools:'Settings'}[view];
  try {if(view==='tools') await loadTools();else await refresh();}catch(error){toast(error.message,true);}
}
async function refresh() {
  if(busy) return; busy=true;
  try {await Promise.all([loadLibrary(),loadJobs()]);$('connection').textContent='Local service connected';$('connection-dot').classList.add('connected');}
  catch(error){$('connection').textContent='Service disconnected';$('connection-dot').classList.remove('connected');throw error;}
  finally{busy=false;}
}
async function saveResponse(response, filename) {
  const blob=await response.blob(), url=URL.createObjectURL(blob), link=document.createElement('a');link.href=url;link.download=filename;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);
}
function alignLibraryWithCollection(inputs) {
  const keywords = inputs.every(value=>! /^(?:https?:\/\/|doi:|10\.\d{4,9}\/)/i.test(value));
  $('region-filter').value = keywords ? $('search-region').value : 'all';
}
async function exportLibrary(ids = []) {
  try {const format=$('export-format').value;const response=await api('/export?' + new URLSearchParams({format,ids:ids.join(','),q:ids.length?'':$('library-search').value,kind:ids.length?'':$('kind-filter').value,region:ids.length?'all':$('region-filter').value}));const path=decodeURIComponent(response.headers.get('X-Artifact-Path'));$('export-location').textContent=`Exported ${response.headers.get('X-Artifact-Count')} item(s): ${path}`;toast('Export saved to selected location');}catch(error){toast(error.message,true);}
}
document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>switchView(button.dataset.view));
document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>{selectedMode=button.dataset.mode;document.querySelectorAll('[data-mode]').forEach(row=>row.classList.toggle('selected',row===button));});
$('collect-form').onsubmit=async(event)=>{event.preventDefault();const inputs=$('collection-input').value.split(/\n+/).map(value=>value.trim()).filter(Boolean);if(!inputs.length)return;const button=$('collect-button');button.disabled=true;try{await json('/jobs',{method:'POST',body:JSON.stringify({inputs,mode:selectedMode,region:$('search-region').value,max_results:Number($('max-results').value),strategy:$('search-strategy').value,platforms:$('search-platform').value?[$('search-platform').value]:[],english_query:$('english-query').value,bilingual:$('bilingual').checked,enrich:false,render:$('render').value,operation:$('crawl-toggle').checked?'crawl':'collect',max_pages:Number($('max-pages').value),depth:Number($('depth').value)})});alignLibraryWithCollection(inputs);toast('Collection task created');await refresh();}catch(error){toast(error.message,true);}finally{button.disabled=false;}};
$('collection-input').addEventListener('keydown',event=>{if(event.ctrlKey&&event.key==='Enter'){$('collect-form').requestSubmit();event.preventDefault();}});
$('library-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>loadLibrary().catch(error=>toast(error.message,true)),250);};
$('kind-filter').onchange=()=>loadLibrary().catch(error=>toast(error.message,true));
$('region-filter').onchange=()=>loadLibrary().catch(error=>toast(error.message,true));
$('close-inspector').onclick=()=>{$('inspector').hidden=true;selectedId='';loadLibrary().catch(()=>{});};
document.addEventListener('keydown',event=>{if(event.key==='Escape')$('close-inspector').click();});
$('export-all').onclick=()=>exportLibrary();
$('refresh-jobs').onclick=()=>refresh().catch(error=>toast(error.message,true));
$('choose-storage').onclick=async()=>{const button=$('choose-storage');button.disabled=true;try {const result=await json('/storage/choose-folder',{method:'POST'});if(result.path){$('storage-path').value=result.path;toast('Folder selected, please click Save Settings');}}catch(error){toast(error.message,true);}finally{button.disabled=false;}};
$('settings-form').onsubmit=async(event)=>{event.preventDefault();try{await json('/settings',{method:'PUT',body:JSON.stringify({storage_root:$('storage-path').value,browser_cache:$('browser-path').value,browser_profile:$('browser-profile').value,domain_interval:Number($('domain-interval').value),max_download_mb:Number($('download-limit').value)})});toast('Settings saved, future tasks will use the new location');await loadTools();}catch(error){toast(error.message,true);}};
refresh().catch(error=>toast(error.message,true));
loadTools().catch(()=>{});
setInterval(()=>refresh().catch(()=>{}),800);

$('search-region').onchange=()=>loadPlatformCatalog().catch(error=>toast(error.message,true));
$('search-strategy').onchange=()=>{$('max-results').value=$('search-strategy').value==='broad'?60:8;};

$('open-browser-login').onclick=async()=>{try{const result=await json('/browser/login',{method:'POST',body:JSON.stringify({url:$('browser-login-url').value})});toast(result.message);}catch(error){toast(error.message,true);}};
