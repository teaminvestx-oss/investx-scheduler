/* InvestX Economic Calendar — Formato aprobado (ES)
   - Lunes: Semanal por días (A)
   - Mar–Vie: Diario simple (B)
   - Fines de semana: opcionalmente no ejecuta (BLOCK_WEEKENDS=1)
   Requiere env: INVESTX_TOKEN, CHAT_ID
   Opcional:
     COUNTRY=USD                 // "USD" o "USD,EUR,GBP"
     IMPACT_MIN=medium|high      // mínimo impacto
     BLOCK_WEEKENDS=1            // no ejecutar sáb/dom
*/

let _fetch = global.fetch, _FormData = global.FormData, _AbortController = global.AbortController;

async function ensureHTTP(){
  if (_fetch && _FormData && _AbortController) return;
  try {
    const undici = await import('undici');
    _fetch = _fetch || undici.fetch;
    _FormData = _FormData || undici.FormData;
    _AbortController = _AbortController || undici.AbortController;
    console.log('[bootstrap] undici cargado');
  } catch {
    console.error('No hay fetch nativo y no se pudo cargar undici. Usa Node >=18 o instala undici.');
    process.exit(1);
  }
}

/* ================== Zona horaria y helpers ================== */
const TZ = 'Europe/Madrid';

const fmtDateISO = d => new Intl.DateTimeFormat('sv-SE', { timeZone: TZ, dateStyle:'short' }).format(d);  // yyyy-mm-dd
const fmtDateES  = d => new Intl.DateTimeFormat('es-ES', { timeZone: TZ, day:'2-digit', month:'2-digit', year:'numeric' }).format(d);
const fmtTime    = d => new Intl.DateTimeFormat('es-ES', { timeZone: TZ, hour:'2-digit', minute:'2-digit', hour12:false }).format(d);
const weekdayES  = d => {
  const s = new Intl.DateTimeFormat('es-ES', { timeZone: TZ, weekday:'long' }).format(d);
  return s.charAt(0).toUpperCase() + s.slice(1);
};
const weekdayShortES = d => new Intl.DateTimeFormat('es-ES', { timeZone: TZ, weekday:'short' }).format(d);

function isMonday(){
  return new Intl.DateTimeFormat('en-GB', { timeZone: TZ, weekday:'short' })
           .format(new Date()).toLowerCase()==='mon';
}
function isWeekend(){
  const wd = new Intl.DateTimeFormat('en-US', { timeZone: TZ, weekday:'short' })
               .format(new Date()).toLowerCase();
  return wd==='sat' || wd==='sun';
}
function weekRangeES(){
  const d = new Date();
  const wd = ['sun','mon','tue','wed','thu','fri','sat']
    .indexOf(new Intl.DateTimeFormat('en-US',{timeZone:TZ,weekday:'short'}).format(d).toLowerCase());
  const diff = wd===0 ? -6 : 1-wd;
  const mon = new Date(d); mon.setDate(d.getDate()+diff);
  const sun = new Date(mon); sun.setDate(mon.getDate()+6);
  return { monday: fmtDateES(mon), sunday: fmtDateES(sun) };
}

/* ================== HTTP con timeout + reintentos ================== */
async function fetchWithTimeout(url, {
  timeoutMs=15000, retries=2, retryDelayBaseMs=800, method='GET', headers={}, body
}={}){
  await ensureHTTP();
  let lastErr;
  for (let i=0;i<=retries;i++){
    const ctrl = new _AbortController();
    const timer = setTimeout(()=>ctrl.abort(new Error('Timeout')), timeoutMs);
    try{
      const res = await _fetch(url, { method, headers, body, signal: ctrl.signal });
      clearTimeout(timer);
      if (!res.ok) {
        let txt=''; try{ txt = await res.text(); }catch{}
        throw new Error(`HTTP ${res.status}${txt?` — ${txt.slice(0,200)}`:''}`);
      }
      return res;
    }catch(e){
      clearTimeout(timer);
      lastErr = e;
      if (i<retries) await new Promise(r=>setTimeout(r, retryDelayBaseMs*(i+1)));
    }
  }
  throw lastErr || new Error('fetch failed');
}

/* ================== Fuente: ForexFactory JSON (semana) ================== */
async function fetchFFWeek(){
  const url = `https://nfs.faireconomy.media/ff_calendar_thisweek.json?_=${Date.now()}`;
  const res = await fetchWithTimeout(url, { headers: { 'User-Agent':'Mozilla/5.0', 'Accept':'application/json' }});
  return res.json();
}

/* ================== Parsing robusto fecha/hora ================== */
function parseDateFromFF(e){
  const raw = e.timestamp;
  if (typeof raw === 'number' && isFinite(raw) && raw>0) return new Date(raw*1000);
  if (typeof raw === 'string' && /^\d+$/.test(raw))     return new Date(Number(raw)*1000);
  if (typeof e.datetime === 'string' && !Number.isNaN(Date.parse(e.datetime))) return new Date(e.datetime);
  if (e.date && e.time){
    const ts = Date.parse(`${e.date} ${e.time}`);
    if (!Number.isNaN(ts)) return new Date(ts);
  }
  return null; // mejor omitir que inventar hora
}

/* ================== Traducción / iconos ================== */
function translateTitleES(t){
  const s = t.trim(); const x = s.toLowerCase();
  const repl = [
    [/^unemployment claims\b/i, 'Peticiones de subsidio por desempleo'],
    [/^continuing jobless claims\b/i, 'Peticiones continuadas de subsidio'],
    [/non-?farm.*(payroll|employment)/i, 'Empleo no agrícola (NFP)'],
    [/^unemployment rate\b/i, 'Tasa de desempleo'],
    [/average hourly earnings.*m\/m/i, 'Salario medio por hora m/m'],
    [/average hourly earnings.*y\/y/i, 'Salario medio por hora a/a'],
    [/fomc.*minutes/i, 'Minutas del FOMC'],
    [/fed chair.*speaks|powell.*speaks|remarks/i, 'Discurso de Powell (Fed)'],
    [/^trade balance\b|^goods trade balance\b/i, 'Balanza comercial'],
    [/^exports\b/i, 'Exportaciones'],
    [/^imports\b/i, 'Importaciones'],
    [/^ism.*services.*pmi/i, 'ISM de servicios'],
    [/^ism.*manufacturing.*pmi/i, 'ISM manufacturero'],
    [/^jolts.*openings/i, 'Vacantes JOLTS'],
    [/core.*cpi.*m\/m/i, 'IPC subyacente m/m'],
    [/core.*cpi.*y\/y/i, 'IPC subyacente a/a'],
    [/^cpi.*m\/m/i, 'IPC m/m'],
    [/^cpi.*y\/y/i, 'IPC a/a'],
    [/core.*pce.*m\/m/i, 'Índice PCE subyacente m/m'],
    [/^retail sales.*m\/m/i, 'Ventas minoristas m/m'],
  ];
  for (const [rx, es] of repl) if (rx.test(s)) return es;
  return s;
}
function decorateTitle(t){
  const x = t.toLowerCase();
  if (/(cpi|ipc|pce|inflación)/i.test(t)) return '📊 ' + escapeTxt(t);
  if (/(nfp|empleo no agrícola|payroll)/i.test(x)) return '📊 ' + escapeTxt(t);
  if (/(fomc|powell|fed)/i.test(x)) return '🗣️ ' + escapeTxt(t);
  return escapeTxt(t);
}
function escapeTxt(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function impactToStars(impact){ return /high/i.test(impact) ? '⭐️⭐️⭐️' : '⭐️⭐️'; }

/* ================== Filtros y normalización ================== */
function filterEvents(raw, onlyToday){
  const todayISO = fmtDateISO(new Date());
  const countries = (process.env.COUNTRY||'USD').split(',').map(s=>s.trim().toUpperCase()).filter(Boolean);
  const impactMin = (process.env.IMPACT_MIN||'medium').toLowerCase();
  const rank = v => /high/i.test(v) ? 2 : /medium/i.test(v) ? 1 : 0;
  const minRank = impactMin==='high' ? 2 : 1;

  const seenKey = new Set();
  const out = [];

  for (const e of raw){
    if (!countries.includes((e.country||'').toUpperCase())) continue;
    if (rank(e.impact||'') < minRank) continue;

    const dt = parseDateFromFF(e);
    if (!dt) continue;

    const rec = {
      ts: dt.getTime(),
      dateISO: fmtDateISO(dt),
      dateES: fmtDateES(dt),
      timeES: fmtTime(dt),
      weekdayES: weekdayES(dt),
      titleES: translateTitleES(String(e.title||'')),
      impactStars: impactToStars(e.impact||''),
    };

    if (onlyToday && rec.dateISO !== todayISO) continue;

    // dedupe: clave por (fechaISO, horaES, título normalizado)
    const k = `${rec.dateISO}|${rec.timeES}|${rec.titleES.toLowerCase()}`;
    if (seenKey.has(k)) continue;
    seenKey.add(k);

    out.push(rec);
  }

  out.sort((a,b)=> (a.ts - b.ts) || a.titleES.localeCompare(b.titleES));
  return out;
}

/* ================== Formateadores (A y B) ================== */
function buildWeeklyA(events){
  const tz = 'Europe/Madrid';
  const {monday, sunday} = weekRangeES();
  if (!events.length){
    return `🗓️ Calendario Económico (🇺🇸) — Semana ${monday}–${sunday} (${tz})\nNo hay eventos de EE. UU. con el filtro actual.`;
  }

  // agrupar por día
  const map = new Map();
  for (const ev of events){
    const key = `${ev.weekdayES} ${ev.dateES}`;
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(ev);
  }

  const lines = [
    `🗓️ Calendario Económico (🇺🇸) — Semana ${monday}–${sunday} (${tz})`,
    `Impacto: ⭐️⭐️ (medio) · ⭐️⭐️⭐️ (alto)`,
    ''
  ];

  for (const [day, arr] of map){
    lines.push(`${day}`);
    // si hay demasiados, limita a 5 por día y añade “+n más…”
    const MAX = 5;
    const slice = arr.slice(0, MAX);
    for (const ev of slice){
      lines.push(`• ${ev.timeES} — ${ev.impactStars} — ${decorateTitle(ev.titleES)}`);
    }
    if (arr.length > MAX) lines.push(`  +${arr.length - MAX} más…`);
    lines.push('');
  }

  let txt = lines.join('\n').trim();
  if (txt.length > 3900) txt = txt.slice(0, 3870) + '\n…recortado';
  return txt;
}

function buildDailyB(events){
  const tz = 'Europe/Madrid';
  if (!events.length){
    return `🗓️ Calendario (🇺🇸) — Hoy ${fmtDateES(new Date())} (${tz})\nHoy no hay eventos de EE. UU.`;
  }
  const lines = [
    `🗓️ Calendario (🇺🇸) — Hoy ${events[0].dateES} (${tz})`,
    `Impacto: ⭐️⭐️ / ⭐️⭐️⭐️`,
    ''
  ];
  for (const ev of events){
    lines.push(`• ${ev.timeES} — ${ev.impactStars} — ${decorateTitle(ev.titleES)}`);
  }
  let txt = lines.join('\n').trim();
  if (txt.length > 3900) txt = txt.slice(0, 3870) + '\n…recortado';
  return txt;
}

/* ================== Telegram ================== */
async function sendTelegramText(token, chatId, text){
  await ensureHTTP();
  const url = `https://api.telegram.org/bot${token}/sendMessage`;
  const body = new URLSearchParams({
    chat_id: chatId, text, parse_mode:'HTML', disable_web_page_preview:'true'
  });
  const res = await fetchWithTimeout(url, { method:'POST', body, headers:{'Content-Type':'application/x-www-form-urlencoded'} });
  const json = await res.json();
  if (!json.ok) throw new Error(`sendMessage Telegram error: ${JSON.stringify(json)}`);
  console.log('Telegram OK');
}

/* ================== Main ================== */
(async ()=>{
  if ((process.env.BLOCK_WEEKENDS||'').trim()==='1' && isWeekend()){
    console.log('Fin de semana (Europe/Madrid) → no ejecuto.');
    return;
  }

  const token = process.env.INVESTX_TOKEN, chatId = process.env.CHAT_ID;
  if (!token || !chatId){
    console.error('Faltan INVESTX_TOKEN / CHAT_ID');
    process.exit(1);
  }

  const weekly = isMonday(); // Lunes: semanal (A) — resto: diario (B)
  console.log(`[${new Date().toISOString()}] weekly=${weekly} — descargando feed…`);
  const raw = await fetchFFWeek();

  const events = filterEvents(raw, !weekly);
  console.log(`Eventos tras filtro: ${events.length}`);

  const msg = weekly ? buildWeeklyA(events) : buildDailyB(events);
  await sendTelegramText(token, chatId, msg);

  console.log('Fin OK');
})().catch(e=>{ console.error('ERROR:', e && e.stack ? e.stack : e); process.exit(1); });
