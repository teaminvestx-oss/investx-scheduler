/* =========================================================================
   📅 InvestX Economic Calendar — ForexFactory (JSON)
   CommonJS puro (Node 18+ con fetch nativo)
   - Lunes: semanal · Mar–Vie: diario · Finde: opcional (BLOCK_WEEKENDS=1)
   - Rango forzado: FORCE_DATE_FROM / FORCE_DATE_TO (YYYY-MM-DD)
   - Mezcla thisweek + nextweek
   - Filtro USD + impacto (Medium/High)
   - VERBOSE=1 para diagnósticos (incluye escaneo USD)
   ========================================================================= */

const TZ = process.env.TZ || 'Europe/Madrid';
const VERBOSE = (process.env.VERBOSE || process.env.LOG_VERBOSE || '')
  .toString().toLowerCase() === '1' || (process.env.VERBOSE||'').toLowerCase()==='true';

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

function fmtDateISO(d) {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat('sv-SE', { timeZone: TZ, dateStyle: 'short' })
      .formatToParts(d).map(x=>[x.type,x.value])
  );
  return `${p.year}-${p.month}-${p.day}`;
}
const fmtDateES = (d) => new Intl.DateTimeFormat('es-ES',{timeZone:TZ,day:'2-digit',month:'2-digit',year:'numeric'}).format(d);
const fmtTime   = (d) => new Intl.DateTimeFormat('es-ES',{timeZone:TZ,hour:'2-digit',minute:'2-digit',hour12:false}).format(d);
const weekdayES = (d) => { const s=new Intl.DateTimeFormat('es-ES',{timeZone:TZ,weekday:'long'}).format(d); return s[0].toUpperCase()+s.slice(1); };
const isMonday  = () => new Intl.DateTimeFormat('en-GB',{timeZone:TZ,weekday:'short'}).format(new Date()).toLowerCase()==='mon';
const isWeekend = () => ['sat','sun'].includes(new Intl.DateTimeFormat('en-US',{timeZone:TZ,weekday:'short'}).format(new Date()).toLowerCase());

function weekRangeDates(){
  const d=new Date();
  const wd=['sun','mon','tue','wed','thu','fri','sat'].indexOf(new Intl.DateTimeFormat('en-US',{timeZone:TZ,weekday:'short'}).format(d).toLowerCase());
  const diff=wd===0?-6:1-wd;
  const mon=new Date(d); mon.setDate(d.getDate()+diff);
  const sun=new Date(mon); sun.setDate(mon.getDate()+6);
  return { mon, sun };
}
function weekMondayISO(dateISO){
  const d=new Date(dateISO+'T00:00:00');
  const wd=['sun','mon','tue','wed','thu','fri','sat'].indexOf(new Intl.DateTimeFormat('en-US',{timeZone:TZ,weekday:'short'}).format(d).toLowerCase());
  const diff=wd===0?-6:1-wd;
  const mon=new Date(d); mon.setDate(d.getDate()+diff);
  return fmtDateISO(mon);
}

/* ------------ fetch con timeout ------------ */
async function fetchWithTimeout(url,{timeoutMs=15000,retries=2,headers={},method='GET',body}={}){
  let last;
  for(let i=0;i<=retries;i++){
    const ctrl=new AbortController();
    const t=setTimeout(()=>ctrl.abort(new Error('Timeout')),timeoutMs);
    try{
      const r=await fetch(url,{signal:ctrl.signal,headers,method,body});
      clearTimeout(t);
      if(!r.ok) throw new Error(`HTTP ${r.status}`);
      return r;
    }catch(e){ clearTimeout(t); last=e; if(i<retries) await sleep(600*(i+1)); }
  }
  throw last||new Error('fetch failed');
}

/* ------------ feeds FF ------------ */
async function fetchFFWeek(){
  const r=await fetchWithTimeout(`https://nfs.faireconomy.media/ff_calendar_thisweek.json?_=${Date.now()}`,{headers:{'User-Agent':'Mozilla/5.0','Accept':'application/json'},timeoutMs:20000});
  const j=await r.json(); if(VERBOSE) console.log('thisweek items:', Array.isArray(j)?j.length:0); return j;
}
async function fetchFFNextWeek(){
  const r=await fetchWithTimeout(`https://nfs.faireconomy.media/ff_calendar_nextweek.json?_=${Date.now()}`,{headers:{'User-Agent':'Mozilla/5.0','Accept':'application/json'},timeoutMs:20000});
  const j=await r.json(); if(VERBOSE) console.log('nextweek items:', Array.isArray(j)?j.length:0); return j;
}

/* ------------ helpers ------------ */
function impactToStars(impact){
  const s=(impact||'').toString().toLowerCase();
  if(s.includes('high')) return '⭐️⭐️⭐️';
  if(s.includes('medium')) return '⭐️⭐️';
  return '⭐️';
}
function translateTitleES(t){
  const s=(t||'').trim();
  const map=[
    [/^unemployment claims\b/i,'Peticiones de subsidio por desempleo'],
    [/^continuing jobless claims\b/i,'Peticiones continuadas de subsidio'],
    [/non-?farm.*(payroll|employment)/i,'Empleo no agrícola (NFP)'],
    [/^unemployment rate\b/i,'Tasa de desempleo'],
    [/average hourly earnings.*m\/m/i,'Salario medio por hora m/m'],
    [/average hourly earnings.*y\/y/i,'Salario medio por hora a/a'],
    [/fomc.*minutes/i,'Minutas del FOMC'],
    [/powell|fed chair.*speaks|remarks/i,'Discurso de Powell (Fed)'],
    [/^trade balance\b/i,'Balanza comercial'], [/^exports\b/i,'Exportaciones'], [/^imports\b/i,'Importaciones'],
    [/^ism.*services.*pmi/i,'ISM de servicios'], [/^ism.*manufacturing.*pmi/i,'ISM manufacturero'],
    [/^jolts.*openings/i,'Vacantes JOLTS'],
    [/^cpi.*m\/m/i,'IPC m/m'], [/^cpi.*y\/y/i,'IPC a/a'], [/core.*cpi.*m\/m/i,'IPC subyacente m/m'], [/core.*cpi.*y\/y/i,'IPC subyacente a/a'],
    [/core.*pce.*m\/m/i,'Índice PCE subyacente m/m'], [/^retail sales.*m\/m/i,'Ventas minoristas m/m'],
  ];
  for(const [rx,es] of map) if(rx.test(s)) return es;
  return s;
}
function decorateTitleES(t){ const x=t.toLowerCase(); if(/ipc|cpi|inflaci|pce/.test(x)) return '📊 '+t; if(/nfp|no agrícola|payroll|desempleo/.test(x)) return '📊 '+t; if(/fomc|powell|fed/.test(x)) return '🗣️ '+t; return t; }

/* ------------ construir eventos ------------ */
function buildEventsFromFF(raw,{fromISO,toISO,impactMin='medium'}){
  const wantHighOnly = (impactMin||'medium').toLowerCase()==='high';

  let cntUSDAll=0, cntUSDImpact=0, cntUSDRango=0; // diagnóstico

  const out=[];
  for(const e of (raw||[])){
    // 1) USD por cualquiera de estos campos:
    const cc  = ((e.country||e.countryCode||'')+'').toUpperCase();  // ej. 'USD' o 'US'
    const cur = ((e.currency||'')+'').toUpperCase();                // ej. 'USD'
    const name= (e.countryName||e.country||'');
    const isUSD = cc==='USD' || cc==='US' || cur==='USD' || /united\s*states|estados\s*unidos/i.test(name);
    if(!isUSD) continue;
    cntUSDAll++;

    // 2) Impacto: Medium/High
    const imp=(e.impact||'').toString().toLowerCase();
    const rank = imp.includes('high') ? 2 : imp.includes('medium') ? 1 : 0;
    if (rank===0) continue;
    if (wantHighOnly && rank<2) continue;
    cntUSDImpact++;

    // 3) Fecha: usar timestamp o date; comparar por día en TZ Madrid
    let dt=null;
    if (e.timestamp) {
      const ts=Number(e.timestamp)||0;
      if (ts) dt=new Date(ts*1000);
    }
    if (!dt && e.date){
      const d = new Date(String(e.date)); // incluye offset, ok
      if(!isNaN(d.getTime())) dt=d;
    }
    if (!dt) continue;

    const dayISOinTZ = fmtDateISO(dt); // día del evento en TZ Madrid
    if (dayISOinTZ < fromISO || dayISOinTZ > toISO) continue;
    cntUSDRango++;

    out.push({
      dayKey: dayISOinTZ,
      dayLabel: `${weekdayES(dt)} ${fmtDateES(dt)}`,
      time: fmtTime(dt),
      stars: impactToStars(e.impact),
      title: decorateTitleES(translateTitleES(e.title||'')),
      id: e.id || e.newsId || e.newsid || null
    });
  }

  if (VERBOSE) {
    console.log(`DBG · USD en feed: ${cntUSDAll} | USD con impacto>=min: ${cntUSDImpact} | USD en rango: ${cntUSDRango}`);
  }

  out.sort((a,b)=> a.dayKey.localeCompare(b.dayKey) || a.time.localeCompare(b.time));
  return out;
}

/* ------------ formato mensaje ------------ */
function limitTelegram(s){ return s.length>3900 ? s.slice(0,3870)+'\n…recortado' : s; }
function buildWeeklyMessageWithHeader(events, header){
  const head=`🗓️ <b>Calendario Económico (🇺🇸)</b> — ${header} (${TZ})\nImpacto: ⭐️⭐️ (medio) · ⭐️⭐️⭐️ (alto)\n\n`;
  if(!events.length) return `${head}No hay eventos de EE. UU. con el filtro actual.`;
  const map=new Map(); for(const e of events){ if(!map.has(e.dayLabel)) map.set(e.dayLabel,[]); map.get(e.dayLabel).push(e); }
  const lines=[head];
  for(const [day,arr] of map){
    lines.push(`<b>${day}</b>`);
    const MAX=5;
    for(const ev of arr.slice(0,MAX)) lines.push(`• ${ev.time} — ${ev.stars} — ${ev.title}`);
    if(arr.length>MAX) lines.push(`  +${arr.length-MAX} más…`);
    lines.push('');
  }
  return limitTelegram(lines.join('\n').trim());
}

/* ------------ Telegram ------------ */
async function sendTelegramText(token, chatId, html){
  const url=`https://api.telegram.org/bot${token}/sendMessage`;
  const body=new URLSearchParams({chat_id:chatId,text:html,parse_mode:'HTML',disable_web_page_preview:'true'});
  const r=await fetch(url,{method:'POST', body});
  if(!r.ok){ const t=await r.text().catch(()=> ''); throw new Error(`Telegram ${r.status} ${t}`); }
}

/* ===================== MAIN ===================== */
(async ()=>{
  try{
    if((process.env.BLOCK_WEEKENDS||'').trim()==='1' && isWeekend()){ console.log('Fin de semana → no ejecuto.'); return; }

    const token=process.env.INVESTX_TOKEN, chatId=process.env.CHAT_ID;
    if(!token||!chatId) throw new Error('Faltan INVESTX_TOKEN / CHAT_ID');

    const weekly=isMonday();

    // Rango (forzado o normal)
    let fromISO, toISO, headerLabel='';
    if(process.env.FORCE_DATE_FROM && process.env.FORCE_DATE_TO){
      fromISO=process.env.FORCE_DATE_FROM.trim(); toISO=process.env.FORCE_DATE_TO.trim();
      headerLabel = `Rango ${fmtDateES(new Date(fromISO+'T00:00:00'))}–${fmtDateES(new Date(toISO+'T00:00:00'))}`;
      console.log('CFG:', { fromISO, toISO, impact: process.env.IMPACT_MIN||'medium', tz: TZ, verbose: VERBOSE });
      console.log(`🧰 Prueba ACTIVADA: ${fromISO}→${toISO}`);
    } else if (weekly){
      const {mon,sun}=weekRangeDates(); fromISO=fmtDateISO(mon); toISO=fmtDateISO(sun);
      headerLabel = `Semana ${fmtDateES(mon)}–${fmtDateES(sun)}`;
      console.log('CFG:', { weekly:true, fromISO, toISO, impact: process.env.IMPACT_MIN||'medium', tz: TZ, verbose: VERBOSE });
    } else {
      const d=new Date(); fromISO=fmtDateISO(d); toISO=fmtDateISO(d);
      headerLabel = `Hoy ${fmtDateES(d)}`;
      console.log('CFG:', { daily:true, fromISO, toISO, impact: process.env.IMPACT_MIN||'medium', tz: TZ, verbose: VERBOSE });
    }

    // Descarga feeds (thisweek + nextweek si hace falta)
    let raw=[];
    const thisW = await fetchFFWeek(); if(Array.isArray(thisW)) raw=raw.concat(thisW);
    const thisMonISO = weekMondayISO(fmtDateISO(new Date()));
    const forceMonISO = weekMondayISO(fromISO);
    const needNext = !!process.env.FORCE_DATE_FROM || ( (new Date(forceMonISO) - new Date(thisMonISO))/(86400000) >= 6 );
    try{
      if(needNext){
        const nextW = await fetchFFNextWeek();
        if(Array.isArray(nextW)) raw = raw.concat(nextW);
      }
    }catch(e){ console.warn('Aviso: nextweek no disponible:', e.message); }
    console.log('Total items raw (this+next):', raw.length);

    // DEBUG: muestra primeros objetos y USD muestreados
    if(VERBOSE && raw.length){
      console.log('--- Primeros elementos del feed ForexFactory ---');
      for(const e of raw.slice(0,5)){
        console.log({
          id: e.id || e.newsId || e.newsid,
          title: e.title,
          country: e.country,
          countryCode: e.countryCode,
          currency: e.currency,
          impact: e.impact,
          timestamp: e.timestamp,
          date: e.date
        });
      }
      console.log('-----------------------------------------------');

      const onlyUSD = raw.filter(e=>{
        const cc  = ((e.country||e.countryCode||'')+'').toUpperCase();
        const cur = ((e.currency||'')+'').toUpperCase();
        const name= (e.countryName||e.country||'');
        return cc==='USD'||cc==='US'||cur==='USD'||/united\s*states|estados\s*unidos/i.test(name);
      });
      console.log(`USD totales en feed: ${onlyUSD.length}`);
      for(const e of onlyUSD.slice(0,5)){
        console.log({ title:e.title, impact:e.impact, date:e.date, timestamp:e.timestamp });
      }
      console.log('-----------------------------------------------');
    }

    const events = buildEventsFromFF(raw,{fromISO,toISO,impactMin:(process.env.IMPACT_MIN||'medium')});
    console.log('Eventos FF dentro de rango:', events.length);
    if(VERBOSE) console.log('sample events:', events.slice(0,3));

    const msg = buildWeeklyMessageWithHeader(events, headerLabel);
    await sendTelegramText(token, chatId, msg);
    console.log('Telegram OK · Fin');
  }catch(err){
    console.error('ERROR:', err && err.stack || err);
    process.exit(1);
  }
})();
