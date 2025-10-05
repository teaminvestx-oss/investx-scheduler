/* =============================================================
   InvestX – Calendario Económico (🇺🇸) vía ForexFactory
   CERO dependencias (Node 18+ con fetch)
   ============================================================= */

const VERBOSE = /^(1|true)$/i.test(process.env.VERBOSE || '');
const SHOW_DESC = /^(1|true)$/i.test(process.env.SHOW_DESC || '');
const IMPACT_MIN = (process.env.IMPACT_MIN || 'medium').toLowerCase(); // medium | high
const TZ = process.env.TZ || 'Europe/Madrid';
const BOT_TOKEN = process.env.INVESTX_TOKEN;
const CHAT_ID = process.env.CHAT_ID;
const DEEPL_API_KEY = process.env.DEEPL_API_KEY || '';

if (!BOT_TOKEN || !CHAT_ID) {
  console.error('Faltan INVESTX_TOKEN / CHAT_ID');
  process.exit(1);
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

/* ---------- Fechas / TZ ---------- */
function fmtDateISO(d) {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat('sv-SE', { timeZone: TZ, dateStyle: 'short' })
      .formatToParts(d).map(x=>[x.type,x.value])
  );
  return `${p.year}-${p.month}-${p.day}`;
}
const fmtDateES = (d) => new Intl.DateTimeFormat('es-ES',{timeZone:TZ,day:'2-digit',month:'2-digit',year:'numeric'}).format(d);
const fmtTimeES = (d) => new Intl.DateTimeFormat('es-ES',{timeZone:TZ,hour:'2-digit',minute:'2-digit',hour12:false}).format(d);
const weekdayES = (d) => { const s=new Intl.DateTimeFormat('es-ES',{timeZone:TZ,weekday:'long'}).format(d); return s[0].toUpperCase()+s.slice(1); };
const isMonday  = () => new Intl.DateTimeFormat('en-GB',{timeZone:TZ,weekday:'short'}).format(new Date()).toLowerCase()==='mon';

function weekRangeDates(){
  const d = new Date();
  const wd = ['sun','mon','tue','wed','thu','fri','sat']
    .indexOf(new Intl.DateTimeFormat('en-US',{timeZone:TZ,weekday:'short'}).format(d).toLowerCase());
  const diff = wd===0 ? -6 : 1-wd;
  const mon = new Date(d); mon.setDate(d.getDate()+diff);
  const sun = new Date(mon); sun.setDate(mon.getDate()+6);
  return { mon, sun };
}
function weekMondayISO(dateISO){
  const d=new Date(dateISO+'T00:00:00');
  const wd=['sun','mon','tue','wed','thu','fri','sat']
    .indexOf(new Intl.DateTimeFormat('en-US',{timeZone:TZ,weekday:'short'}).format(d).toLowerCase());
  const diff=wd===0?-6:1-wd;
  const mon=new Date(d); mon.setDate(d.getDate()+diff);
  return fmtDateISO(mon);
}

/* ---------- Fetch con timeout ---------- */
async function fetchWithTimeout(url, { timeoutMs=20000, retries=2, headers={}, method='GET', body } = {}){
  let last;
  for (let i=0;i<=retries;i++){
    const ctrl = new AbortController();
    const t = setTimeout(()=>ctrl.abort(new Error('Timeout')), timeoutMs);
    try{
      const res = await fetch(url, { signal: ctrl.signal, headers, method, body });
      clearTimeout(t);
      if(!res.ok) throw new Error(`HTTP ${res.status}`);
      return res;
    }catch(e){ clearTimeout(t); last=e; if(i<retries) await sleep(700*(i+1)); }
  }
  throw last || new Error('fetch failed');
}

/* ---------- ForexFactory feeds ---------- */
async function fetchFFWeek(){
  const r = await fetchWithTimeout(`https://nfs.faireconomy.media/ff_calendar_thisweek.json?_=${Date.now()}`,{
    headers:{'User-Agent':'Mozilla/5.0','Accept':'application/json'}});
  const j = await r.json();
  if (VERBOSE) console.log('thisweek items:', Array.isArray(j)?j.length:0);
  return j;
}
async function fetchFFNextWeek(){
  const r = await fetchWithTimeout(`https://nfs.faireconomy.media/ff_calendar_nextweek.json?_=${Date.now()}`,{
    headers:{'User-Agent':'Mozilla/5.0','Accept':'application/json'}});
  const j = await r.json();
  if (VERBOSE) console.log('nextweek items:', Array.isArray(j)?j.length:0);
  return j;
}

/* ---------- Impacto / hora ---------- */
function impactToStars(impact){
  const s = (impact||'').toString().toLowerCase();
  if (s.includes('high')) return '⭐️⭐️⭐️';
  if (s.includes('medium')) return '⭐️⭐️';
  return '⭐️';
}
function isTypicalUSAtHalf(title) {
  const s = (title || '').toLowerCase();
  return /unemployment claims|jobless claims|non-?farm|payroll|nfp|cpi|consumer price|pce|retail sales|core pce|ppi|producer price/.test(s);
}
function fmtTimeUS(dt, title){
  const snapped = new Date(Math.round(dt.getTime()/60000)*60000);
  let [hh,mm] = fmtTimeES(snapped).split(':').map(Number);
  if (isTypicalUSAtHalf(title)) mm = 30;
  return `${String(hh).padStart(2,'0')}:${String(mm).padStart(2,'0')}`;
}

/* ---------- DeepL ---------- */
async function deeplTranslate(text, targetLang='es'){
  if (!DEEPL_API_KEY) return text;
  try{
    const url = 'https://api-free.deepl.com/v2/translate';
    const body = new URLSearchParams({ auth_key: DEEPL_API_KEY, text, target_lang: targetLang.toUpperCase() });
    const r = await fetchWithTimeout(url, { method:'POST', body, timeoutMs:20000 });
    const j = await r.json();
    const t = j?.translations?.[0]?.text;
    return t || text;
  }catch(_){ return text; }
}

/* ---------- Why Traders Care (heurística de slug) ---------- */
function slugifyTitle(t){
  return (t||'').toLowerCase()
    .replace(/&/g,' and ')
    .replace(/[^a-z0-9\s-]/g,'')
    .replace(/\s+/g,' ')
    .trim()
    .replace(/\s/g,'-');
}
async function fetchWhyTradersCare(country, title){
  if (!SHOW_DESC) return null;
  if (!/us|united states|usd/i.test(country||'')) return null;
  const slug = `us-${slugifyTitle(title)}`;
  const url = `https://www.forexfactory.com/calendar/${slug}`;
  try{
    const r = await fetchWithTimeout(url, { timeoutMs: 12000, headers:{'User-Agent':'Mozilla/5.0'} });
    const html = await r.text();
    const m = html.match(/Why\s+Traders\s+Care<\/[^>]+>([\s\S]*?)<\/(?:div|td|section)>/i);
    if (!m) return null;
    const raw = m[1].replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').trim();
    if (!raw) return null;
    const es = await deeplTranslate(raw, 'es');
    return es;
  }catch(_){ return null; }
}

/* ---------- Filtros ---------- */
function inImpact(impact){
  const s=(impact||'').toLowerCase();
  if (IMPACT_MIN==='high') return s.includes('high');
  return s.includes('medium') || s.includes('high');
}
function isUSD(e){
  const cc  = ((e.country||e.countryCode||'')+'').toUpperCase();
  const cur = ((e.currency||'')+'').toUpperCase();
  const name= (e.countryName||e.country||'');
  return cc==='USD' || cc==='US' || cur==='USD' || /united\s*states|estados\s*unidos/i.test(name);
}

/* ---------- Construcción de eventos ---------- */
async function buildEvents(fromISO, toISO){
  let raw=[];
  const thisW = await fetchFFWeek(); if(Array.isArray(thisW)) raw=raw.concat(thisW);

  const thisMonISO = weekMondayISO(fmtDateISO(new Date()));
  const forceMonISO= weekMondayISO(fromISO);
  const needNext = !!process.env.FORCE_DATE_FROM || ( (new Date(forceMonISO) - new Date(thisMonISO))/(86400000) >= 6 );
  try{
    if(needNext){ const nextW=await fetchFFNextWeek(); if(Array.isArray(nextW)) raw=raw.concat(nextW); }
  }catch(e){ if(VERBOSE) console.log('Aviso: nextweek no disponible:', e.message); }

  if (VERBOSE){
    console.log('Total items raw:', raw.length);
    console.log('Muestra 3:', raw.slice(0,3).map(x=>({title:x.title,country:x.country,impact:x.impact,date:x.date})));
  }

  const out=[];
  for (const e of (raw||[])){
    if (!isUSD(e)) continue;
    if (!inImpact(e.impact)) continue;

    let dt=null;
    if (e.timestamp) {
      const ts=Number(e.timestamp)||0;
      if (ts) dt=new Date(ts*1000);
    }
    if (!dt && e.date){
      const d = new Date(String(e.date));
      if(!isNaN(d.getTime())) dt=d;
    }
    if (!dt) continue;

    const dayKey = fmtDateISO(dt);
    if (dayKey < fromISO || dayKey > toISO) continue;

    const timeLocal = fmtTimeUS(dt, e.title||'');
    const titleES = (await deeplTranslate(e.title||'', 'es')).trim();
    let why = null;
    if (SHOW_DESC) {
      why = await fetchWhyTradersCare(e.country||'US', e.title||'');
      if (!why) {
        // fallback breve
        const s=(e.title||'').toLowerCase();
        if (/unemployment claims|jobless claims/.test(s)) why='Solicitudes semanales de paro (indicador de ciclo).';
        else if (/non-?farm|payroll|nfp/.test(s))       why='Empleo no agrícola: referencia mensual clave.';
        else if (/unemployment rate/.test(s))           why='Porcentaje de parados vs fuerza laboral.';
        else if (/average hourly earnings/.test(s))     why='Crecimiento salarial (presión inflacionaria).';
      }
    }

    out.push({
      dayKey,
      dayLabel: `${weekdayES(dt)} ${fmtDateES(dt)}`,
      time: timeLocal,
      stars: impactToStars(e.impact),
      title: titleES || (e.title||''),
      desc: why
    });
  }

  out.sort((a,b)=> a.dayKey.localeCompare(b.dayKey) || a.time.localeCompare(b.time));
  return out;
}

/* ---------- Mensaje ---------- */
function limitTelegram(s){ return s.length>3900 ? s.slice(0,3870)+'\n…recortado' : s; }
function buildMessage(events, header){
  const head=`🗓️ <b>Calendario Económico (🇺🇸)</b> — ${header} (${TZ})\nImpacto: ⭐️⭐️ (medio) · ⭐️⭐️⭐️ (alto)\n\n`;
  if(!events.length) return `${head}No hay eventos de EE. UU. con el filtro actual.`;
  const map=new Map(); for(const e of events){ if(!map.has(e.dayLabel)) map.set(e.dayLabel,[]); map.get(e.dayLabel).push(e); }
  const lines=[head];
  for(const [day,arr] of map){
    lines.push(`<b>${day}</b>`);
    for(const ev of arr){
      lines.push(`• ${ev.time} — ${ev.stars} — ${ev.title}`);
      if (ev.desc) lines.push(`  · ${ev.desc}`);
    }
    lines.push('');
  }
  return limitTelegram(lines.join('\n').trim());
}

/* ---------- Telegram ---------- */
async function sendTelegramText(token, chatId, html){
  const url=`https://api.telegram.org/bot${token}/sendMessage`;
  const body=new URLSearchParams({chat_id:chatId,text:html,parse_mode:'HTML',disable_web_page_preview:'true'});
  const r=await fetch(url,{method:'POST', body});
  const t=await r.text().catch(()=> '');
  if(!r.ok) throw new Error(`Telegram ${r.status} ${t}`);
}

/* ===================== MAIN ===================== */
(async ()=>{
  try{
    let fromISO, toISO, headerLabel='';
    if(process.env.FORCE_DATE_FROM && process.env.FORCE_DATE_TO){
      fromISO=process.env.FORCE_DATE_FROM.trim(); toISO=process.env.FORCE_DATE_TO.trim();
      headerLabel = `Rango ${fmtDateES(new Date(fromISO+'T00:00:00'))}–${fmtDateES(new Date(toISO+'T00:00:00'))}`;
      if (VERBOSE) console.log('🧰 Prueba ACTIVADA:', fromISO,'→',toISO);
    } else if (isMonday()){
      const {mon,sun}=weekRangeDates(); fromISO=fmtDateISO(mon); toISO=fmtDateISO(sun);
      headerLabel = `Semana ${fmtDateES(mon)}–${fmtDateES(sun)}`;
    } else {
      const d=new Date(); fromISO=fmtDateISO(d); toISO=fmtDateISO(d);
      headerLabel = `Hoy ${fmtDateES(d)}`;
    }

    const events = await buildEvents(fromISO, toISO);
    if (VERBOSE) {
      console.log('Eventos USD seleccionados:', events.length);
      console.log('Sample:', events.slice(0,3));
    }

    const msg = buildMessage(events, headerLabel);
    await sendTelegramText(BOT_TOKEN, CHAT_ID, msg);
    console.log('Telegram OK · Fin');
  }catch(err){
    console.error('ERROR:', err && err.stack || err);
    process.exit(1);
  }
})();
