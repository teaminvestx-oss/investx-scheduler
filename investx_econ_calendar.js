/* InvestX Economic Calendar — feed JSON (ForexFactory) con timeouts y reintentos
   Requiere env: INVESTX_TOKEN, CHAT_ID
   Opcional: VERIFY_TELEGRAM=1 (ping de prueba)
*/

const fs = require('fs');
const path = require('path');
const PImage = require('pureimage');

/* ====== Polyfills HTTP (fetch/FormData/Blob/AbortController) vía undici si faltan ====== */
let _fetch = global.fetch;
let _FormData = global.FormData;
let _Blob = global.Blob;
let _AbortController = global.AbortController;

async function ensureHTTPPolyfills(){
  if (_fetch && _FormData && _Blob && _AbortController) return;
  try {
    const undici = await import('undici');
    _fetch = _fetch || undici.fetch;
    _FormData = _FormData || undici.FormData;
    _Blob = _Blob || undici.Blob;
    _AbortController = _AbortController || undici.AbortController;
    console.log('[bootstrap] Usando undici como polyfill de fetch/FormData/Blob/AbortController');
  } catch (e) {
    console.error('No hay fetch/FormData/Blob nativos y falló undici. Instala undici o usa Node >= 18.');
    process.exit(1);
  }
}

/* ================== util: zona horaria y formatos ================== */
const TZ = 'Europe/Madrid';
const NOW = () => {
  const d = new Date();
  const p = Object.fromEntries(new Intl.DateTimeFormat('sv-SE', {
    timeZone: TZ, year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'
  }).formatToParts(d).map(x=>[x.type,x.value]));
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`;
};

const fmtDate = d => new Intl.DateTimeFormat('sv-SE', { timeZone: TZ, dateStyle: 'short' }).format(d);
const fmtTime = d => new Intl.DateTimeFormat('sv-SE', { timeZone: TZ, hour: '2-digit', minute: '2-digit' }).format(d);
const isMonday = () => new Intl.DateTimeFormat('en-GB', { timeZone: TZ, weekday:'short' }).format(new Date()).toLowerCase()==='mon';
const weekRangeISO = () => {
  const d = new Date();
  const wd = ['sun','mon','tue','wed','thu','fri','sat']
    .indexOf(new Intl.DateTimeFormat('en-US',{timeZone:TZ,weekday:'short'}).format(d).toLowerCase());
  const diff = wd===0 ? -6 : 1-wd;
  const mon = new Date(d); mon.setDate(d.getDate()+diff);
  const sun = new Date(mon); sun.setDate(mon.getDate()+6);
  return { monday: fmtDate(mon), sunday: fmtDate(sun) };
};

/* ================== helper: timeout promisificado ================== */
function withTimeout(promise, ms, label='op') {
  let t;
  const timeout = new Promise((_, rej) => {
    t = setTimeout(() => rej(new Error(`timeout ${label} ${ms}ms`)), ms);
  });
  return Promise.race([promise.finally(() => clearTimeout(t)), timeout]);
}

/* ================== fetch con timeout + reintentos genérico ================== */
async function fetchWithTimeout(url, {
  timeoutMs = 15000,
  retries = 2,
  retryDelayBaseMs = 800,   // backoff: base * (intento+1)
  method = 'GET',
  headers = {},
  body = undefined,
} = {}) {
  await ensureHTTPPolyfills();
  let lastErr;
  for (let i = 0; i <= retries; i++) {
    const ctrl = new _AbortController();
    const timer = setTimeout(() => ctrl.abort(new Error('Timeout')), timeoutMs);
    try {
      const res = await _fetch(url, { method, headers, body, signal: ctrl.signal });
      clearTimeout(timer);
      if (!res.ok) {
        let txt = '';
        try { txt = await res.text(); } catch(_){}
        throw new Error(`HTTP ${res.status}${txt ? ` — ${txt.slice(0,200)}`:''}`);
      }
      return res;
    } catch (e) {
      clearTimeout(timer);
      lastErr = e;
      if (i < retries) {
        const delay = retryDelayBaseMs * (i + 1);
        await new Promise(r => setTimeout(r, delay));
      }
    }
  }
  throw lastErr || new Error('fetch failed');
}

/* ================== Datos: ForexFactory JSON (semana) ================== */
async function fetchFFWeek() {
  const url = `https://nfs.faireconomy.media/ff_calendar_thisweek.json?_=${Date.now()}`;
  const res = await fetchWithTimeout(url, {
    timeoutMs: 15000,
    retries: 2,
    headers: { 'User-Agent':'Mozilla/5.0', 'Accept':'application/json' }
  });
  return res.json();
}

function filterEvents(raw, onlyToday) {
  const todayStr = fmtDate(new Date());
  return raw
    .filter(e => (e.country||'').toUpperCase()==='USD')
    .filter(e => /medium|high/i.test(e.impact||''))
    .map(e => {
      const ts = (Number(e.timestamp)||0)*1000;
      const dt = ts ? new Date(ts) : new Date();
      return {
        date: fmtDate(dt),
        time: fmtTime(dt),
        title: (e.title||'').trim(),
        forecast: (e.forecast??'').toString().trim(),
        previous: (e.previous??'').toString().trim(),
        impact: (e.impact||'').toLowerCase()
      };
    })
    .filter(e => onlyToday ? e.date===todayStr : true)
    .sort((a,b)=>(a.date+a.time).localeCompare(b.date+b.time));
}

/* ================== Imagen PNG (opcional) ================== */
// ✅ Nueva versión: solo confía en la promesa de encodePNGToStream; sin 'finish' manual
async function drawPNG(events, caption){
  try{
    const width=1200,rowH=56,headerH=100,shown=Math.min(events.length,22);
    const h=headerH+rowH*shown+40;
    const img=PImage.make(width,h); const ctx=img.getContext('2d');

    // fondo
    ctx.fillStyle='#fff'; ctx.fillRect(0,0,width,h);

    // fuente (best effort)
    const f='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf';
    if (fs.existsSync(f)) { const font=PImage.registerFont(f,'UI'); await font.load(); }

    // cabecera
    ctx.fillStyle='#111'; ctx.font='32pt UI, Arial';
    ctx.fillText('Calendario económico USA (⭐️⭐️/⭐️⭐️⭐️)',28,56);
    ctx.font='18pt UI, Arial'; ctx.fillStyle='#444'; ctx.fillText(caption,28,86);

    // encabezados
    ctx.fillStyle='#222'; ctx.font='16pt UI, Arial';
    ctx.fillText('Fecha',28,headerH); ctx.fillText('Hora',140,headerH);
    ctx.fillText('Evento',230,headerH); ctx.fillText('Forecast',900,headerH); ctx.fillText('Previo',1040,headerH);
    ctx.strokeStyle='#e5e7eb'; ctx.beginPath(); ctx.moveTo(20,headerH+10); ctx.lineTo(width-20,headerH+10); ctx.stroke();

    // filas
    ctx.font='15pt UI, Arial';
    let y=headerH+40;
    for (const e of events.slice(0,shown)) {
      ctx.fillStyle='#111'; ctx.fillText(e.date,28,y); ctx.fillText(e.time,140,y);
      const maxW=650; let t=e.title||'';
      while (t.length && ctx.measureText(t+'…').width>maxW) t=t.slice(0,-1);
      if ((e.title||'').length!==t.length) t+='…';
      ctx.fillText(t,230,y);
      ctx.fillStyle='#2563eb'; ctx.fillText(e.forecast||'-',900,y);
      ctx.fillStyle='#6b7280'; ctx.fillText(e.previous||'-',1040,y);
      ctx.strokeStyle='#f3f4f6'; ctx.beginPath(); ctx.moveTo(20,y+14); ctx.lineTo(width-20,y+14); ctx.stroke();
      y+=rowH;
    }

    // exportar (confía en la promesa; no esperes 'finish' manual)
    const out = fs.createWriteStream('calendar.png');
    await PImage.encodePNGToStream(img, out);
    return true;
  }catch(e){
    console.error('PNG generation failed:', e.message);
    return false;
  }
}

/* ================== Texto resumen ================== */
function buildSummary(events, onlyToday){
  if(!events.length) return '';
  const top=events.slice(0,4);
  return "📰 <b>Resumen principales noticias</b>\n\n" + top.map(e=>{
    const meta=[]; if(e.forecast) meta.push(`consenso ${e.forecast}`); if(e.previous) meta.push(`anterior ${e.previous}`);
    const extra=meta.length?` — ${meta.join(', ')}`:'';
    return `📌 <b>${e.title}</b> (${onlyToday?e.time:`${e.date} ${e.time}`})${extra}`;
  }).join("\n\n");
}

/* ================== Telegram helpers (con reintentos) ================== */
async function sendTelegramPhoto(token, chatId, caption, filePath){
  await ensureHTTPPolyfills();
  const url=`https://api.telegram.org/bot${token}/sendPhoto`;

  const form = new _FormData();
  form.append('chat_id', chatId);
  form.append('caption', caption);
  form.append('parse_mode', 'HTML');

  const fileBuf = fs.readFileSync(filePath);
  form.append('photo', new _Blob([fileBuf]), path.basename(filePath));

  const res = await fetchWithTimeout(url, {
    method: 'POST',
    body: form,
    timeoutMs: 15000,
    retries: 2,
  });
  const json = await res.json();
  if (!json.ok) throw new Error(`sendPhoto Telegram error: ${JSON.stringify(json)}`);
  console.log('Telegram photo OK');
}

async function sendTelegramText(token, chatId, html){
  await ensureHTTPPolyfills();
  const url=`https://api.telegram.org/bot${token}/sendMessage`;
  const body = new URLSearchParams({
    chat_id: chatId,
    text: html,
    parse_mode: 'HTML',
    disable_web_page_preview: 'true'
  });
  const res = await fetchWithTimeout(url, {
    method: 'POST',
    body,
    timeoutMs: 15000,
    retries: 2,
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
  });
  const json = await res.json();
  if (!json.ok) throw new Error(`sendMessage Telegram error: ${JSON.stringify(json)}`);
  console.log('Telegram text OK');
}

async function verifyTelegram(token, chatId){
  if(!process.env.VERIFY_TELEGRAM) return;
  console.log('VERIFY_TELEGRAM=1 → ping de prueba…');
  await sendTelegramText(token, chatId, '✅ InvestX cron conectado (ping).');
}

/* ================== Main con watchdog y fallback ================== */
(async ()=>{
  // watchdog global: mata proceso a los 3 min (ajusta si quieres)
  const watchdog = setTimeout(()=>{ 
    console.error('Watchdog: timeout global alcanzado, salgo.'); 
    process.exit(1); 
  }, 3*60*1000);

  const token=process.env.INVESTX_TOKEN, chatId=process.env.CHAT_ID;
  if(!token||!chatId){ 
    console.error('Faltan INVESTX_TOKEN / CHAT_ID'); 
    clearTimeout(watchdog); 
    process.exit(1); 
  }

  console.log(`[${NOW()}] Start. CHAT_ID=${chatId}`);
  await verifyTelegram(token, chatId);

  const weekly=isMonday();
  console.log('Descargando feed semanal (FF)…');
  const raw=await fetchFFWeek();
  console.log(`Items recibidos: ${raw.length}`);

  const events=filterEvents(raw, !weekly);
  console.log(`Filtrados USD + medium/high: ${events.length} (onlyToday=${!weekly})`);

  const {monday,sunday}=weekRangeISO();
  const caption = weekly
    ? `🗓️ Calendario USA (⭐️⭐️/⭐️⭐️⭐️) — Semana ${monday}–${sunday}`
    : `🗓️ Calendario USA (⭐️⭐️/⭐️⭐️⭐️) — Hoy ${fmtDate(new Date())}`;

  let sentPhoto=false;
  if(events.length){
    console.log('Generando PNG…');
    let ok=false;
    try {
      // timeout corto para evitar bloqueos de pureimage
      ok = await withTimeout(drawPNG(events, caption), 20000, 'drawPNG');
    } catch (e) {
      console.error('drawPNG timeout/fallo:', e.message);
    }
    if(ok && fs.existsSync('calendar.png')){
      console.log('Enviando PNG…');
      try {
        await sendTelegramPhoto(token, chatId, caption, 'calendar.png');
        sentPhoto=true;
      } catch (e) {
        console.error('Fallo enviando PNG, continúo con texto:', e.message);
      }
    } else {
      console.log('PNG no generado a tiempo, continúo con texto.');
    }
  } else {
    console.log('No hay eventos tras filtros.');
  }

  const summary=buildSummary(events, !weekly);
  if(summary){
    console.log('Enviando resumen…');
    await sendTelegramText(token, chatId, summary);
  } else if(!sentPhoto){
    console.log('Enviando aviso sin eventos…');
    await sendTelegramText(token, chatId, `🗓️ ${caption}\n\n(No hay eventos relevantes).`);
  }

  console.log('OK fin cron.');
  clearTimeout(watchdog);
})().catch(err=>{ 
  console.error('ERROR:', err && err.stack ? err.stack : err); 
  process.exit(1); 
});
