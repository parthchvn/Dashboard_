/* Application state and read-only API wiring. Every displayed datum is escaped. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const {dateWindow,displayDates,parseWallets}=window.XviFilters;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const price=v=>Number.isFinite(v)?`${(v*100).toFixed(1)}¢`:'—';
  const number=v=>Number(v||0).toLocaleString('en-US');
  const money=v=>'$'+new Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:2}).format(v||0);
  const stamp=t=>t===null||t===undefined?'—':new Intl.DateTimeFormat('en-US',{
    timeZone:'UTC',month:'short',day:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',hour12:false
  }).format(new Date(t*1000));
  const state={mode:'markets',market:null,event:null,audit:null,series:null,start:null,end:null,offset:0,sequence:0,eventSequence:0,librarySequence:0,charts:[],wallets:[],walletRole:'either',dateDirty:false};
  let controller,searchTimer,toastTimer;
  const chart=new MarketChart($('price-chart'),{tooltip:$('chart-tooltip'),navigator:$('navigator'),onRange:setRange,onReset:resetRange});

  function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,7000);}
  async function api(path,signal){const response=await fetch(path,{signal});if(!response.ok){let message=`Request failed (${response.status})`;try{const doc=await response.json();if(typeof doc.detail==='string')message=doc.detail;}catch{}throw new Error(message);}return response.json();}
  function showDialog(id){$(id).showModal();}
  function headline(m){
    $('market-title').textContent=m.question||`Market ${m.market_id}`;$('market-eyebrow').textContent=m.event_title||'MARKET EXPLORER';
    $('market-id').textContent=`Market ${m.market_id}`;
    $('market-state').textContent=m.metadata_outcome?`Closed metadata indicates ${m.metadata_outcome} · settlement time ${m.resolution_ts?stamp(m.resolution_ts):'unavailable'}`:'Historical executions · not live';
    const text=`Traded ${m.answer1||'outcome 1'} price`;$('price-label').textContent=`Last ${text.toLowerCase()}`;$('chart-title').textContent=state.wallets.length?`Wallet-filtered ${text.toLowerCase()}`:text;
    $('chart-description').textContent=state.wallets.length?'Matching fills only. Not the complete market price path.':'Original fills. Outcome-1 equivalent.';
    $('comparison-tab').disabled=!m.event_id;
  }
  function priceView(){
    state.eventSequence++;state.charts.forEach(c=>c.destroy());state.charts=[];
    $('market-view').hidden=false;$('event-view').hidden=true;$('price-tab').classList.add('active');$('comparison-tab').classList.remove('active');
    if(state.market)headline(state.market);
  }
  function clearWindow(){
    state.series=null;chart.series=null;chart.points=[];chart.reviews=[];chart.geometry=null;
    chart.canvas.getContext('2d').clearRect(0,0,chart.canvas.width,chart.canvas.height);
    for(const id of ['metric-price','metric-change','metric-notional','metric-fills','resolution-badge','coverage-number','tape-page'])$(id).textContent='—';
    $('metric-change').className='';$('price-time').textContent='Loading selected window';$('metric-transactions').textContent='Distinct transactions';
    $('coverage-bar').style.width='0%';$('trade-body').replaceChildren();$('prev-trades').disabled=true;$('next-trades').disabled=true;$('export').disabled=true;
  }
  function markSelected(id){document.querySelectorAll('.market-item').forEach(item=>item.classList.toggle('active',item.dataset.id===id));}


  function walletQuery(){return state.wallets.length?{wallets:state.wallets.join(','),wallet_role:state.walletRole}:{};}
  function clearFilterError(){
    $('filter-error').hidden=true;
    for(const id of ['range-start','range-end','wallet-ids'])$(id).removeAttribute('aria-invalid');
  }
  function syncDates(data){
    const dates=displayDates(data.start,data.end);
    $('range-start').value=dates.initial;$('range-end').value=dates.final;state.dateDirty=false;
    $('date-note').textContent=(data.start%86400===0&&data.end%86400===0)?
      'Both dates are included, in UTC. You can still zoom into the chart.':
      'Dates contain the displayed window. Editing them selects whole UTC days, including the final day.';
  }
  function renderFilters(){
    const count=state.wallets.length,roles={either:'maker or taker',maker:'maker only',taker:'taker only'};
    $('filter-badge').textContent=count?`${count} WALLET${count===1?'':'S'}`:'ALL WALLETS';
    $('clear-wallets').hidden=!count;$('filter-scope').hidden=!count;$('show-reviews').disabled=!!count;
    $('filter-scope').textContent=`Wallet filter active · ${count} address${count===1?'':'es'} · ${roles[state.walletRole]}. Chart, volume, statistics, tape, and export include matching fills only. Directions still describe the recorded taker.`;
  }
  async function applyFilters(event){
    event.preventDefault();if(!state.market)return;clearFilterError();
    let dates,wallets;
    try{dates=dateWindow($('range-start').value,$('range-end').value);}
    catch(error){$('range-start').setAttribute('aria-invalid','true');$('range-end').setAttribute('aria-invalid','true');$('filter-error').textContent=error.message;$('filter-error').hidden=false;return;}
    try{wallets=parseWallets($('wallet-ids').value);}
    catch(error){$('wallet-ids').setAttribute('aria-invalid','true');$('filter-error').textContent=error.message;$('filter-error').hidden=false;return;}
    if(state.dateDirty||!state.series){state.start=dates.start;state.end=dates.end;}
    const role=$('wallet-role').value;
    const changed=wallets.join(',')!==state.wallets.join(',')||role!==state.walletRole;
    state.wallets=wallets;state.walletRole=role;state.offset=0;
    $('wallet-ids').value=wallets.join(', ');renderFilters();
    if(changed)chart.overview=null;
    await loadSeries(changed);
  }
  function filterWallet(address,role='either'){
    if(!/^0x[0-9a-f]{40}$/i.test(address||''))return;
    state.wallets=[address.toLowerCase()];state.walletRole=role;state.offset=0;
    $('wallet-ids').value=state.wallets[0];$('wallet-role').value=role;chart.overview=null;
    clearFilterError();renderFilters();loadSeries(true);
  }
  function walletButton(address,role){
    const button=document.createElement('button');button.type='button';button.className='wallet-button';
    button.textContent=address?`${address.slice(0,6)}…${address.slice(-4)}`:'—';
    button.title=`Filter ${role} wallet: ${address||'unavailable'}`;
    button.setAttribute('aria-label',button.title);button.dataset.wallet=address||'';button.dataset.role=role;
    button.classList.toggle('matched',state.wallets.includes((address||'').toLowerCase())&&(state.walletRole==='either'||state.walletRole===role));
    button.disabled=!/^0x[0-9a-f]{40}$/i.test(address||'');
    button.addEventListener('click',e=>{e.stopPropagation();filterWallet(address,role);});return button;
  }

  async function library(){
    const sequence=++state.librarySequence,mode=state.mode,q=$('search').value.trim();
    try{
      const items=await api(`/api/${mode}?q=${encodeURIComponent(q)}`);if(sequence!==state.librarySequence)return;
      $('library-count').textContent=items.length;$('library').replaceChildren();
      if(!items.length){$('library').innerHTML='<p class="muted">No imported matches. Search by ID, or ingest the market first.</p>';return items;}
      for(const item of items){
        const event=mode==='events',id=event?item.event_id:item.market_id,title=event?item.title:item.question;
        const button=document.createElement('button');button.className='market-item';button.dataset.id=id;
        button.classList.toggle('active',event?state.event===id:state.market?.market_id===id);
        button.innerHTML=`<span class="market-glyph">${esc(event?'EV':(item.answer1||'1').slice(0,1).toUpperCase())}</span><span class="item-copy"><span class="item-title">${esc(title)}</span><span class="item-sub"><span>${event?`${item.siblings} outcomes`:esc(id)}</span><strong>${event?money(item.notional):price(item.last_price)}</strong></span></span>`;
        button.addEventListener('click',()=>event?openEvent(id):openMarket(id));$('library').append(button);
      }
      $('library-note').textContent=`Showing ${items.length} imported ${mode}. Search by name or exact ID.`;return items;
    }catch(error){if(sequence===state.librarySequence){$('library').textContent=error.message;toast(error.message);}}
  }
  function switchLibrary(mode){state.mode=mode;for(const type of ['markets','events']){$(`${type}-tab`).classList.toggle('active',type===mode);$(`${type}-tab`).setAttribute('aria-selected',String(type===mode));}library();}
  async function openMarket(id,initialRange=null,initialFilters=null){
    state.market={market_id:id};state.event=null;state.start=initialRange?.start??null;state.end=initialRange?.end??null;state.offset=0;
    state.wallets=initialFilters?.wallets||[];state.walletRole=initialFilters?.role||'either';state.dateDirty=false;
    $('wallet-ids').value=state.wallets.join(', ');$('wallet-role').value=state.walletRole;clearFilterError();renderFilters();
    chart.overview=null;clearWindow();priceView();$('dashboard').hidden=false;$('onboarding').hidden=true;markSelected(id);
    $('level').value='auto';await loadSeries(true);
  }
  function queryWindow(){return new URLSearchParams({
    ...(state.start!==null?{start:state.start,end:state.end}:{}),...walletQuery(),level:$('level').value,
    target:Math.max(100,Math.min(3000,Math.round($('price-chart').getBoundingClientRect().width*4)))
  });}
  function updateHash(){if(!state.market)return;history.replaceState(null,'','#'+new URLSearchParams({market:state.market.market_id,...(state.start!==null?{start:state.start,end:state.end}:{}),...walletQuery()}));}

  async function loadSeries(overview=false){
    if(!state.market)return;
    const sequence=++state.sequence,id=state.market.market_id;controller?.abort();controller=new AbortController();
    clearWindow();$('filter-form').setAttribute('aria-busy','true');
    $('export').disabled=true;$('chart-loading').hidden=false;$('chart-message').hidden=true;$('chart-tooltip').hidden=true;
    try{
      const data=await api(`/api/markets/${encodeURIComponent(id)}/series?${queryWindow()}`,controller.signal);
      if(sequence!==state.sequence)return;
      state.series=data;state.market=data.market;headline(data.market);
      if(data.empty)throw new Error('Metadata exists, but this sibling has no archived fills.');
      chart.staleAfter=Number($('stale').value);chart.set(data,{overview});syncDates(data);renderFilters();
      const stats=data.stats,change=stats.first_price===null?null:(stats.last_price-stats.first_price)*100;
      $('metric-price').textContent=price(stats.last_price);$('price-time').textContent=`${stamp(stats.last_ts)} UTC`;
      $('metric-change').textContent=change===null?'—':`${change>=0?'+':''}${change.toFixed(2)}`;
      $('metric-change').className=change===null?'':change>=0?'positive':'negative';
      $('metric-notional').textContent=money(stats.notional);$('metric-fills').textContent=number(stats.fill_count);
      $('metric-transactions').textContent=`${number(stats.tx_count)} distinct transactions`;
      $('resolution-badge').textContent=`${data.level==='raw'?'RAW FILLS':data.level.toUpperCase()+' BARS'} · ${number(data.rows.length)}`;
      $('window-label').textContent=`${stamp(data.start)} — ${stamp(data.end)} UTC`;
      const slots=Math.floor((data.end-1)/30)-Math.floor(data.start/30)+1,coverage=stats.occupied_30s/slots*100;
      $('coverage-number').textContent=`${coverage.toFixed(1)}%`;$('coverage-bar').style.width=`${coverage}%`;
      $('show-dots').disabled=data.level!=='raw';
      $('show-dots').parentElement.title=data.level==='raw'?'All raw executions are displayed':'Raw execution dots are available when zoomed to raw resolution. These dots are observed-bin closes.';
      if(!data.rows.length){$('chart-message').textContent=state.wallets.length?'No matching wallet fills in this window. Clear wallets or change the dates.':'No executions in this window. No prices were filled in.';$('chart-message').hidden=false;}
      $('export').disabled=false;updateHash();
      await Promise.all([loadTape(sequence),loadReviews(sequence)]);
      if(overview&&state.start!==null){
        const full=await api(`/api/markets/${encodeURIComponent(id)}/series?${new URLSearchParams({target:1400,...walletQuery()})}`,controller.signal);
        if(sequence===state.sequence){chart.overview={points:window.xviChartPoints(full),start:full.start,end:full.end};chart.drawNavigator();}
      }
    }catch(error){
      if(error.name==='AbortError'||sequence!==state.sequence)return;
      clearWindow();$('chart-message').textContent=error.message;$('chart-message').hidden=false;toast(error.message);
    }finally{if(sequence===state.sequence){$('chart-loading').hidden=true;$('filter-form').setAttribute('aria-busy','false');}}
  }
  async function loadTape(sequence=state.sequence){
    const data=state.series;if(!data||data.empty)return;const offset=state.offset;
    const tape=await api(`/api/markets/${encodeURIComponent(state.market.market_id)}/trades?${new URLSearchParams({start:data.start,end:data.end,offset,...walletQuery()})}`,controller?.signal);
    if(sequence!==state.sequence||offset!==state.offset)return;
    $('trade-body').replaceChildren();
    if(!tape.rows.length)$('trade-body').innerHTML='<tr><td colspan="8" class="muted">No matching observed fills in this window.</td></tr>';
    for(const row of tape.rows){
      const tr=document.createElement('tr'),t=new Date(row.timestamp*1000).toISOString();tr.tabIndex=0;
      tr.setAttribute('aria-label',`Inspect ${row.yes_direction} fill at ${stamp(row.timestamp)}`);
      tr.innerHTML=`<td>${esc(t.slice(11,19))}<small>${esc(t.slice(5,10))}</small></td><td><span class="direction ${row.yes_direction==='SELL'?'sell':''}">${row.yes_direction==='SELL'?'↘':'↗'} ${esc(row.yes_direction)}</span></td><td>${price(row.yes_price)}</td><td>${Number(row.token_amount).toLocaleString('en-US',{maximumFractionDigits:2})}</td><td>$${Number(row.usd_amount).toLocaleString('en-US',{maximumFractionDigits:2})}</td><td class="tx">${esc(row.transaction_hash.slice(0,8))}…${esc(row.transaction_hash.slice(-5))} ↗</td>`;
      for(const role of ['maker','taker']){const cell=document.createElement('td');cell.append(walletButton(row[role],role));tr.insertBefore(cell,tr.lastElementChild);}
      tr.addEventListener('click',()=>inspect(row));tr.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target===tr)inspect(row);});$('trade-body').append(tr);
    }
    $('tape-page').textContent=tape.rows.length?`${offset+1}–${offset+tape.rows.length}`:'0 fills';
    $('prev-trades').disabled=offset===0;$('next-trades').disabled=!tape.has_more||offset>=100000;
  }
  function inspect(row){
    const entries=[['Observed at',new Date(row.timestamp*1000).toISOString()],['Block / log',`${row.block_number} / ${row.log_index}`],
      ['Outcome-1 equivalent',`${price(row.yes_price)} · ${row.yes_direction}`],['Original token / action',`${row.nonusdc_side} · ${row.taker_direction}`],
      ['Original price',price(row.raw_price)],['Shares',number(row.token_amount)],['Original cash notional',`$${row.usd_amount}`],
      ['Transaction',row.transaction_hash],['Taker wallet',row.taker],['Maker counterparty',row.maker],['Exchange contract',row.contract]];
    const grid=document.createElement('div');grid.className='details-grid';
    for(const [key,value] of entries){const div=document.createElement('div');if(String(value).length>34)div.className='wide';const label=document.createElement('span');label.textContent=key;const text=document.createElement('strong');text.textContent=value;div.append(label,text);grid.append(div);}
    $('trade-detail').replaceChildren(grid);
    const actions=document.createElement('div');actions.className='inspector-actions';
    for(const role of ['maker','taker']){if(!/^0x[0-9a-f]{40}$/i.test(row[role]||''))continue;const b=document.createElement('button');b.className='button';b.textContent=`Filter this ${role} wallet`;b.addEventListener('click',()=>{$('trade-dialog').close();filterWallet(row[role],role);});actions.append(b);}
    $('trade-detail').append(actions);
    if(/^0x[0-9a-f]{64}$/i.test(row.transaction_hash)&&!state.audit.demo){const a=document.createElement('a');a.href=`https://polygonscan.com/tx/${row.transaction_hash}`;a.target='_blank';a.rel='noopener noreferrer';a.textContent='View transaction on PolygonScan ↗';$('trade-detail').append(a);}
    showDialog('trade-dialog');
  }
  async function loadReviews(sequence=state.sequence){
    chart.reviews=[];const data=state.series;
    if(state.wallets.length){$('review-list').textContent='Market-wide overlays are hidden while wallet filtering is active.';chart.draw();return;}
    if(!$('show-reviews').checked||!data||data.empty){$('review-list').innerHTML='<span class="review-empty">No review overlay loaded.</span>';chart.draw();return;}
    const level=data.level==='raw'?'30s':data.level;
    const doc=await api(`/api/markets/${encodeURIComponent(state.market.market_id)}/reviews?level=${level}&start=${data.start}&end=${data.end}`,controller?.signal);
    if(sequence!==state.sequence||!$('show-reviews').checked)return;
    chart.reviews=doc.annotations||[];chart.draw();$('review-list').replaceChildren();
    if(!doc.available){$('review-list').innerHTML=`<span class="review-empty">No ${esc(level)} overlay in this snapshot.<br>Run the optional news_attr adapter.</span>`;return;}
    const heading=document.createElement('p');heading.textContent=`${doc.annotations.length} review annotations · ${level}`;$('review-list').append(heading);
    for(const item of doc.annotations.slice(0,30)){
      const button=document.createElement('button');button.className='review-record';button.textContent=`${stamp(item.timestamp)} · ${price(item.price)}`;
      button.addEventListener('click',()=>{const pre=document.createElement('pre');pre.textContent=JSON.stringify(item.record,null,2);$('trade-detail').replaceChildren(pre);showDialog('trade-dialog');});$('review-list').append(button);
    }
    if(doc.annotations.length>30){const note=document.createElement('small');note.textContent='First 30 listed; all annotations in the window are plotted.';$('review-list').append(note);}
  }
  async function setRange(start,end){
    if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start)return;
    state.start=Math.max(0,Math.floor(start));state.end=Math.ceil(end);state.offset=0;clearFilterError();await loadSeries();
  }
  async function resetRange(){state.start=null;state.end=null;state.offset=0;state.dateDirty=false;clearFilterError();await loadSeries(true);}

  async function openEvent(eventId){
    const sequence=++state.eventSequence;state.sequence++;controller?.abort();state.event=eventId;
    state.charts.forEach(c=>c.destroy());state.charts=[];markSelected(eventId);
    $('dashboard').hidden=false;$('onboarding').hidden=true;$('market-view').hidden=true;$('event-view').hidden=false;
    $('price-tab').classList.remove('active');$('comparison-tab').classList.add('active');$('comparison-tab').disabled=false;$('export').disabled=true;
    $('event-cards').textContent='Loading archived siblings…';history.replaceState(null,'',`#event=${encodeURIComponent(eventId)}`);
    try{
      const event=await api(`/api/events/${encodeURIComponent(eventId)}?stale_after=${$('stale').value}`);if(sequence!==state.eventSequence)return;
      if(!state.market||state.market.event_id!==eventId){state.market=event.siblings.find(m=>m.fill_count>0)||null;state.start=null;state.end=null;state.wallets=[];state.walletRole='either';$('wallet-ids').value='';$('wallet-role').value='either';renderFilters();chart.overview=null;}
      $('market-title').textContent=event.title;$('market-eyebrow').textContent='EVENT COMPARISON';$('market-id').textContent=`Event ${eventId}`;
      $('market-state').textContent=`${event.siblings.length} archived siblings · ${stamp(event.as_of)} UTC`;
      $('event-sum').textContent=event.sum===null?'Incomplete':event.sum.toFixed(3);$('event-sum-note').textContent=`${event.stale_siblings} stale or missing legs · not normalized`;
      $('event-cards').replaceChildren();
      for(const market of event.siblings){
        if(sequence!==state.eventSequence)return;
        const card=document.createElement('article');card.className='event-card';
        card.innerHTML=`<div class="event-card-header"><div><h3>${esc(market.question)}</h3><small>${esc(market.market_id)} · ${market.stale?'stale / missing':'recent at comparison time'}</small></div><strong>${price(market.as_of_price)}</strong></div>`;
        let canvas;
        if(market.fill_count){canvas=document.createElement('canvas');canvas.setAttribute('role','img');canvas.setAttribute('aria-label',`${market.question}: historical traded prices`);card.append(canvas);}
        else{const missing=document.createElement('div');missing.className='event-missing';missing.textContent='Metadata only · no archived fills';card.append(missing);}
        const footer=document.createElement('div');footer.className='event-card-footer';footer.innerHTML=`<span>${number(market.fill_count)} fills · ${money(market.notional)}</span>`;
        const button=document.createElement('button');button.className='quiet';button.textContent='Inspect market ↗';button.disabled=!market.fill_count;button.addEventListener('click',()=>openMarket(market.market_id));footer.append(button);card.append(footer);$('event-cards').append(card);
        if(canvas){
          const info=document.createElement('small');info.textContent='Loading observed history…';card.insertBefore(info,footer);
          // Sequential cache builds prevent one large event from flooding the server.
          try{const series=await api(`/api/markets/${encodeURIComponent(market.market_id)}/series?target=700`);if(sequence!==state.eventSequence)return;
            const mini=new MarketChart(canvas,{compact:true});mini.staleAfter=Number($('stale').value);mini.set(series);state.charts.push(mini);
            info.textContent=`${series.level} observed history · gaps retained`;card.dataset.ready='true';
          }catch(error){info.textContent=error.message;card.dataset.ready='error';}
        }else card.dataset.ready='true';
      }
    }catch(error){if(sequence===state.eventSequence){$('event-cards').textContent=error.message;toast(error.message);}}
  }
  function audit(){
    const a=state.audit;if(!a?.ready){showDialog('data-dialog');return;}
    const entries=[['Source',a.demo?'Synthetic fixture':'Original trades snapshot'],['Measured coverage',`${stamp(a.first_ts)} — ${stamp(a.last_ts)}`],
      ['Displayed fills',number(a.displayed_fills)],['Invalid rows removed',number(a.invalid_rows_removed)],['Duplicate logs removed',number(a.duplicate_logs_removed)],['Exchange rows excluded',number(a.exchange_rows_excluded)]];
    $('audit-content').innerHTML='<div class="details-grid">'+entries.map(([k,v])=>`<div><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join('')+'</div>';
    $('audit-json').textContent=JSON.stringify(a,null,2);showDialog('audit-dialog');
  }
  async function exported(){
    if(!state.series||state.event)return;const data=state.series,id=state.market.market_id;$('export').disabled=true;
    try{const response=await fetch(`/api/markets/${encodeURIComponent(id)}/export.csv?${new URLSearchParams({start:data.start,end:data.end,...walletQuery()})}`);
      if(!response.ok)throw new Error((await response.json()).detail||'Export failed');
      const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download=`${id}-observed-fills.csv`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast(state.wallets.length?'Exported matching wallet fills only.':'Exported observed fills. No carried prices included.');
    }catch(error){toast(error.message);}finally{if(!state.event)$('export').disabled=false;}
  }

  document.querySelectorAll('.close-dialog').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
  for(const id of ['manage-data','setup-help','about'])$(id).addEventListener('click',()=>showDialog('data-dialog'));
  for(const id of ['audit-tab','footer-audit'])$(id).addEventListener('click',audit);
  $('markets-tab').addEventListener('click',()=>switchLibrary('markets'));$('events-tab').addEventListener('click',()=>switchLibrary('events'));
  $('search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(library,220);});
  document.addEventListener('keydown',e=>{if(e.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&!document.querySelector('dialog[open]')){e.preventDefault();$('search').focus();}});
  $('price-tab').addEventListener('click',()=>{if(state.market){priceView();state.event=null;state.offset=0;loadSeries(!chart.overview);}});
  $('comparison-tab').addEventListener('click',()=>{const id=state.market?.event_id||state.event;if(id)openEvent(id);});
  $('reset-zoom').addEventListener('click',resetRange);$('level').addEventListener('change',()=>{state.offset=0;loadSeries();});
  $('stale').addEventListener('change',()=>{chart.staleAfter=Number($('stale').value);chart.draw();});
  $('show-range').addEventListener('change',()=>{chart.showRange=$('show-range').checked;chart.draw();});
  $('show-dots').addEventListener('change',()=>{chart.showDots=$('show-dots').checked;chart.draw();});
  $('show-reviews').addEventListener('change',()=>loadReviews().catch(e=>{if(e.name!=='AbortError')toast(e.message);}));
  $('prev-trades').addEventListener('click',()=>{state.offset=Math.max(0,state.offset-50);loadTape().catch(e=>toast(e.message));});
  $('next-trades').addEventListener('click',()=>{state.offset+=50;loadTape().catch(e=>toast(e.message));});
  $('export').addEventListener('click',exported);
  $('share').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(location.href);toast('View link copied. It opens against the same local archive.');}catch{toast('Copy the current browser address to share this local view.');}});
  $('filter-form').addEventListener('submit',applyFilters);
  $('all-dates').addEventListener('click',resetRange);
  $('clear-wallets').addEventListener('click',()=>{state.wallets=[];state.walletRole='either';$('wallet-ids').value='';$('wallet-role').value='either';state.offset=0;chart.overview=null;clearFilterError();renderFilters();loadSeries(true);});
  for(const id of ['range-start','range-end'])$(id).addEventListener('input',()=>{state.dateDirty=true;clearFilterError();});
  $('wallet-ids').addEventListener('input',clearFilterError);
  $('theme').addEventListener('click',()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=theme;try{localStorage.setItem('xvi-theme',theme);}catch{}chart.draw();state.charts.forEach(c=>c.draw());});
  try{if(localStorage.getItem('xvi-theme')==='dark')document.documentElement.dataset.theme='dark';}catch{}
  async function start(){
    try{const status=await api('/api/status');state.audit=status;
      if(!status.ready){$('connection-status').textContent='No archive connected';$('onboarding').hidden=false;$('library').innerHTML='<p class="muted">Your markets appear here after ingestion.</p>';return;}
      $('connection-status').textContent=status.demo?'Synthetic preview':'Local archive · historical';$('sidebar-source').textContent=status.demo?'Synthetic · isolated demo':`${number(status.market_count)} imported markets`;$('demo-banner').hidden=!status.demo;
      const list=await library(),hash=new URLSearchParams(location.hash.slice(1));
      if(hash.has('event')){await openEvent(hash.get('event'));return;}
      const id=hash.get('market')||list?.[0]?.market_id;
      if(id){const a=Number(hash.get('start')),b=Number(hash.get('end')),wallets=parseWallets(hash.get('wallets')||''),role=hash.get('wallet_role')||'either';
        if(!['either','maker','taker'].includes(role))throw new Error('Wallet role must be either, maker, or taker.');
        await openMarket(id,hash.has('start')&&Number.isFinite(a)&&Number.isFinite(b)&&b>a?{start:a,end:b}:null,{wallets,role});}
      else $('onboarding').hidden=false;
    }catch(error){$('connection-status').textContent='Connection unavailable';$('onboarding').hidden=false;$('library').textContent=error.message;toast(error.message);}
  }
  start();
})();
