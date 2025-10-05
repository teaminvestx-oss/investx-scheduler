/* InvestX Economic Calendar — SOLO ForexFactory (JSON) + descripción opcional (ES)
   Formato: hora — ⭐️ — evento (ES), agrupado por día.
   Lunes => semanal | Mar–Vie => diario | Sáb/Dom => opcionalmente no ejecuta.
   PRUEBA rango: FORCE_DATE_FROM / FORCE_DATE_TO (YYYY-MM-DD).

   ENV requeridos:
     INVESTX_TOKEN, CHAT_ID

   ENV opcionales:
     TZ=Europe/Madrid
     IMPACT_MIN=medium|high
     BLOCK_WEEKENDS=1
     INCLUDE_DETAILS=1                 // añade descripción debajo del evento
     DEEPL_API_KEY=xxxx               // si está, traduce EN->ES con DeepL
     DETAILS_MAX_CHARS=220            // recorte de la descripción
     FORCE_DATE_FROM=YYYY-MM-DD
     FORCE_DATE_TO=YYYY-MM-DD
*/

let _fetch = global.fetch, _FormData = global.FormData, _AbortController = global.AbortController;
async function ensureHTTP(){ if(_fetch&&_FormData&&_AbortController) return; const u=await import('undici'); _fetch=u.fetch; _FormData=u.FormData; _AbortController=u.AbortController; }

const TZ = process.env.TZ || 'Europe/Madrid';
const fmtDateISO = d => new Intl.DateTimeFormat('sv-SE',{timeZone:TZ,dateStyle:'short'}).format(d); // yyyy-mm-dd
const fmtDateES  = d => new Intl.DateTimeFormat('es-ES',{timeZone:TZ,day:'2-digit',month:'2-digit',year:'numeric'}).format(d);
const fmtTime    = d => new Intl.DateTimeFormat('es-ES',{timeZone:TZ,hour:'2-digit',minute:'2-digit',hour12:false}).format(d);
const weekdayES  = d => { const s=new Intl.DateTimeFormat('es-ES',{timeZone:TZ,weekday:'long'}).format(d); return s.charAt(0).toUpperCase()+s.slice(1); };
function isMonday(){return new Intl.DateTimeFormat('en-GB',{timeZone:TZ,weekday:'short'}).format(new Date()).toLowerCase()==='mon';}
function isWeekend(){const w=new Intl.DateTimeFormat('en-US',{timeZone:TZ,weekday:'short'}).format(new Date()).toLowerCase(); return w==='sat'||w==='sun';}
function weekRangeDates(){const d=new Date();const wd=['sun','mon','tue','wed','thu','fri','sat'].indexOf(new Intl.DateTimeFormat('en-US',{timeZone:TZ,weekday:'short'}).format(d).toLowerCase());const diff=wd===0?-6:1-wd;const mon=new Date(d);mon.setDate(d.getDate()+diff);const sun=new Date(mon);sun.setDate(mon.getDate()+6);return{mon,sun};}
function weekRangeES(){const {mon,sun}=weekRangeDates();return{monday:fmtDateES(mon),sunday:fmtDateES(sun)};}

/* ---------- HTTP con timeout y reintentos ---------- */
async function fetchWithTimeout(url,{timeoutMs=15000,retries=2,method='GET',headers={},body}={}){
  await ensureHTTP();
  let lastErr;
  for(let i=0;i<=retries;i++){
    const ctrl=new _AbortController(); const t=setTimeout(()=>ctrl.abort(new Error('Timeout')),timeoutMs);
    try{
      const res=await _fetch(url,{method,headers,body,signal:ctrl.signal});
      clearTimeout(t);
      if(!res.ok){ const txt=await res.text().catch(()=> ''); throw new Error(`HTTP ${res.status}${txt?` — ${txt.slice(0,100)}`:''}`); }
      return res;
    }catch(e){ clearTimeout(t); lastErr=e; if(i<retries) await new Promise(r=>setTimeout(r,800*(i+1))); }
  }
  throw lastErr || new Error('fetch failed');
}

/* ---------- Fuente: ForexFactory JSON (esta semana / próxima) ---------- */
async function fetchFFWeek(){
  const url=`https://nfs.faireconomy.media/ff_calendar_thisweek.json?_=${Date.now()}`;
  const res=await fetchWithTimeout(url,{headers:{'User-Agent':'Mozilla/5.0','Accept':'application/json'}});
  return res.json();
}
async function fetchFFNextWeek(){
  const url=`https://nfs.faireconomy.media/ff_calendar_nextweek.json?_=${Date.now()}`;
  const res=await fetchWithTimeout(url,{headers:{'User-Agent':'Mozilla/5.0','Accept':'application/json'}});
  return res.json();
}
function weekMonday(dateISO){
  const d = new Date(dateISO+'T00:00:00');
  const wd = ['sun','mon','tue','wed','thu','fri','sat']
    .indexOf(new Intl.DateTimeFormat('en-US',{timeZone:TZ,weekday:'short'}).format(d).toLowerCase());
  const diff = wd===0 ? -6 : 1-wd;
  const mon = new Date(d); mon.setDate(d.getDate()+diff);
  return fmtDateISO(mon); // yyyy-mm-dd
}

/* ---------- Traducción + decoración de títulos ---------- */
function impactToStars(impact){ return /high/i.test(impact)?'⭐️⭐️⭐️':'⭐️⭐️'; }
function translateTitleES(t){
  const s=t.trim();
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

/* ---------- Enriquecimiento: descripción desde ficha FF + DeepL ---------- */
async function fetchFFDescription(newsId){
  const urls = [
    `https://www.forexfactory.com/calendar?newsid=${newsId}`,
    `https://www.forexfactory.com/calendar?detail=${newsId}`,
  ];
  for (const u of urls){
    try{
      const r=await fetchWithTimeout(u,{headers:{
        'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36',
        'Accept':'text/html',
        'Accept-Language':'en-US,en;q=0.9'
      }, timeoutMs: 20000});
      const html=await r.text();
      const m = html.match(/<td[^>]*>\s*Description\s*<\/td>\s*<td[^>]*>([\s\S]*?)<\/td>/i)
             || html.match(/<h3[^>]*>\s*Description\s*<\/h3>\s*<p[^>]*>([\s\S]*?)<\/p>/i);
      if (m) {
        const raw = stripTags(m[1]).replace(/\s+/g,' ').trim();
        if (raw) return raw;
      }
    }catch(e){ /* sigue */ }
  }
  return '';
}
async function translateWithDeepL(text){
  const key = process.env.DEEPL_API_KEY;
  if(!key || !text) return text;
  try{
    const body = new URLSearchParams({ text, target_lang:'ES', source_lang:'EN' });
    const res = await fetchWithTimeout(
      'https://api-free.deepl.com/v2/translate',
      { method:'POST', body, headers:{'Authorization':`DeepL-Auth-Key ${key}`, 'Content-Type':'application/x-www-form-urlencoded'}, timeoutMs:15000, retries:1 }
    );
    const json = await res.json();
    return json?.translations?.[0]?.text || text;
  }catch{ return text; }
}
function stripTags(s){ return String(s).replace(/<[^>]*>/g,''); }

/* ---------- Construir eventos desde FF ---------- */
function buildEventsFromFF(ff,{fromISO,toISO,impactMin='medium'}){
  const minRank=impactMin==='high'?2:1; const rank=v=>/high/i.test(v)?2:/medium/i.test(v)?1:0;
  const out=[];
  for(const e of ff){
    if((e.country||'').toUpperCase()!=='USD') continue;
    if(rank(e.impact||'')<minRank) continue;
    const ts=Number(e.timestamp)||0; if(!ts) continue;
    const dt=new Date(ts*1000);
    const dISO=fmtDateISO(dt); if(dISO<fromISO||dISO>toISO) continue;
    out.push({
      dayLabel: `${weekdayES(dt)} ${fmtDateES(dt)}`,
      time: fmtTime(dt),
      stars: impactToStars(e.impact||''),
      title: decorateTitleES(translateTitleES(String(e.title||''))),
      newsId: e.id || e.newsid || e.newsId || null,
    });
  }
  out.sort((a,b)=> a.dayLabel.localeCompare(b.dayLabel) || a.time.localeCompare(b.time));
  return out;
}

/* ---------- Mensajes ---------- */
function limitTelegram(s){ return s.length>3900 ? s.slice(0,3870)+'\n…recortado' : s; }
function buildWeeklyMessage(events){
  const {monday,sunday}=weekRangeES();
  const head=`🗓️ Calendario Económico (🇺🇸) — Semana ${monday}–${sunday} (${TZ})\nImpacto: ⭐️⭐️ (medio) · ⭐️⭐️⭐️ (alto)\n`;
  if(!events.length) return `${head}\nNo hay eventos de EE. UU. con el filtro actual.`;
  const map=new Map(); for(const e of events){ if(!map.has(e.dayLabel)) map.set(e.dayLabel,[]); map.get(e.dayLabel).push(e); }
  const lines=[head];
  for(const [day,arr] of map){
    lines.push(day);
    const MAX=5;
    for(const ev of arr.slice(0,MAX)){
      lines.push(`• ${ev.time} — ${ev.stars} — ${ev.title}`);
      if (ev.descES) lines.push(`   <i>${ev.descES}</i>`);
    }
    if(arr.length>MAX) lines.push(`  +${arr.length-MAX} más…`);
    lines.push('');
  }
  return limitTelegram(lines.join('\n').trim());
}
function buildWeeklyMessageWithCustomHeader(events, rangeLabelES){
  const head=`🗓️ Calendario Económico (🇺🇸) — Rango ${rangeLabelES || ''} (${TZ})\nImpacto: ⭐️⭐️ (medio) · ⭐️⭐️⭐️ (alto)\n`;
  if(!events.length) return `${head}\nNo hay eventos de EE. UU. con el filtro actual.`;
  const map=new Map(); for(const e of events){ if(!map.has(e.dayLabel)) map.set(e.dayLabel,[]); map.get(e.dayLabel).push(e); }
  const lines=[head];
  for(const [day,arr] of map){
    lines.push(day);
    const MAX=5;
    for(const ev of arr.slice(0,MAX)){
      lines.push(`• ${ev.time} — ${ev.stars} — ${ev.title}`);
      if (ev.descES) lines.push(`   <i>${ev.descES}</i>`);
    }
    if(arr.length>MAX) lines.push(`  +${arr.length-MAX} más…`);
    lines.push('');
  }
  return limitTelegram(lines.join('\n').trim());
}
function buildDailyMessage(events){
  const head=`🗓️ Calendario (🇺🇸) — Hoy ${fmtDateES(new Date())} (${TZ})\nImpacto: ⭐️⭐️ / ⭐️⭐️⭐️\n`;
  if(!events.length) return `${head}\nHoy no hay eventos de EE. UU.`;
  const lines=[head];
  for(const ev of events){
    lines.push(`• ${ev.time} — ${ev.stars} — ${ev.title}`);
    if (ev.descES) lines.push(`   <i>${ev.descES}</i>`);
  }
  return limitTelegram(lines.join('\n').trim());
}

/* ---------- Telegram ---------- */
async function sendTelegramText(token,chatId,text){
  await ensureHTTP();
  const url=`https://api.telegram.org/bot${token}/sendMessage`;
  const body=new URLSearchParams({chat_id:chatId,text,parse_mode:'HTML',disable_web_page_preview:'true'});
  const r=await fetchWithTimeout(url,{method:'POST',body,headers:{'Content-Type':'application/x-www-form-urlencoded'}});
  const j=await r.json(); if(!j.ok) throw new Error(JSON.stringify(j));
}

/* ---------- Main ---------- */
(async ()=>{
  if((process.env.BLOCK_WEEKENDS||'').trim()==='1' && isWeekend()){ console.log('Fin de semana → no ejecuto.'); return; }

  const token=process.env.INVESTX_TOKEN, chatId=process.env.CHAT_ID;
  if(!token||!chatId){ console.error('Faltan INVESTX_TOKEN / CHAT_ID'); process.exit(1); }

  const weekly=isMonday();

  // Fechas (rango forzado o normal) + etiqueta de header para rangos forzados
  let fromISO,toISO, headerRangeLabel=null;
  if(process.env.FORCE_DATE_FROM && process.env.FORCE_DATE_TO){
    fromISO=process.env.FORCE_DATE_FROM.trim();
    toISO=process.env.FORCE_DATE_TO.trim();
    headerRangeLabel = `${fmtDateES(new Date(fromISO+'T00:00:00'))}–${fmtDateES(new Date(toISO+'T00:00:00'))}`;
    console.log(`🔧 Prueba: ${fromISO}→${toISO}`);
  } else if(weekly){
    const {mon,sun}=weekRangeDates(); fromISO=fmtDateISO(mon); toISO=fmtDateISO(sun);
  } else {
    const d=new Date(); fromISO=fmtDateISO(d); toISO=fmtDateISO(d);
  }

  // Descargar thisweek y, si corresponde, nextweek y mezclar
  let raw = [];
  try {
    const thisW = await fetchFFWeek(); raw = raw.concat(thisW||[]);
    const thisMonISO = weekMonday(fmtDateISO(new Date()));   // lunes semana actual
    const forceMonISO = weekMonday(fromISO);                 // lunes del rango pedido
    const oneWeekAhead = (a,b) => {
      const A = new Date(a+'T00:00:00').getTime();
      const B = new Date(b+'T00:00:00').getTime();
      const diffDays = Math.round((A-B)/86400000);
      return diffDays >= 6 && diffDays <= 8; // ~7 ±1 por TZ
    };
    if (process.env.FORCE_DATE_FROM && oneWeekAhead(forceMonISO, thisMonISO)) {
      try{
        const nextW = await fetchFFNextWeek();
        raw = raw.concat(nextW||[]);
        console.log(`FF nextweek añadido: ${nextW?.length||0} items`);
      }catch(e){ console.warn('Aviso: no pude cargar ff_calendar_nextweek.json:', e.message); }
    }
  } catch(e){
    console.error('Error descargando FF:', e);
    raw = [];
  }

  // Construir eventos dentro del rango
  let events = buildEventsFromFF(raw,{fromISO,toISO,impactMin:(process.env.IMPACT_MIN||'medium').toLowerCase()});
  console.log(`Eventos FF dentro de rango: ${events.length}`);

  // Enriquecer con descripción si procede
  if ((process.env.INCLUDE_DETAILS||'').trim()==='1' && events.length){
    const maxChars = Number(process.env.DETAILS_MAX_CHARS||'220');
    for (const ev of events){
      if (!ev.newsId) continue;
      try{
        const rawDesc = await fetchFFDescription(ev.newsId);
        if (!rawDesc) continue;
        let textES = rawDesc;
        if (process.env.DEEPL_API_KEY) textES = await translateWithDeepL(rawDesc);
        textES = textES.replace(/\s+/g,' ').trim();
        if (textES.length > maxChars) textES = textES.slice(0, maxChars-1)+'…';
        ev.descES = textES;
      }catch{ /* silencioso */ }
    }
  }

  // Mensaje
  const msg = (process.env.FORCE_DATE_FROM && process.env.FORCE_DATE_TO)
    ? buildWeeklyMessageWithCustomHeader(events, headerRangeLabel)
    : (weekly ? buildWeeklyMessage(events) : buildDailyMessage(events));

  await sendTelegramText(token,chatId,msg);
  console.log('Telegram OK · Fin');
})().catch(e=>{ console.error('ERROR',e); process.exit(1); });
