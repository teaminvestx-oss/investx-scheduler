/* InvestX Economic Calendar — Texto (ES)
   Fuente primaria: Investing (widget). Fallback: ForexFactory JSON.
   Formato: hora — ⭐️ — evento, agrupado por día (en español).
   Lunes => semanal | Mar–Vie => diario | Sáb/Dom => opcionalmente no ejecuta.
   PRUEBA por rango: FORCE_DATE_FROM / FORCE_DATE_TO (YYYY-MM-DD)

   ENV requeridos:
     INVESTX_TOKEN, CHAT_ID
   ENV opcionales:
     TZ=Europe/Madrid
     IMPACT_MIN=medium|high
     BLOCK_WEEKENDS=1
     FORCE_DATE_FROM=YYYY-MM-DD
     FORCE_DATE_TO=YYYY-MM-DD
     SOURCE=auto|investing|ff   (default auto)
*/

let _fetch = global.fetch, _FormData = global.FormData, _AbortController = global.AbortController;
async function ensureHTTP(){ if(_fetch&&_FormData&&_AbortController)return; const u=await import('undici'); _fetch=_fetch||u.fetch; _FormData=_FormData||u.FormData; _AbortController=_AbortController||u.AbortController; }

const TZ = process.env.TZ || 'Europe/Madrid';

// ---------- Fechas / TZ ----------
const fmtDateISO = d => new Intl.DateTimeFormat('sv-SE',{timeZone:TZ,dateStyle:'short'}).format(d); // yyyy-mm-dd
const fmtDateES  = d => new Intl.DateTimeFormat('es-ES',{timeZone:TZ,day:'2-digit',month:'2-digit',year:'numeric'}).format(d);
const fmtTime    = d => new Intl.DateTimeFormat('es-ES',{timeZone:TZ,hour:'2-digit',minute:'2-digit',hour12:false}).format(d);
const weekdayES  = d => { const s=new Intl.DateTimeFormat('es-ES',{timeZone:TZ,weekday:'long'}).format(d); return s.charAt(0).toUpperCase()+s.slice(1); };
function isMonday(){return new Intl.DateTimeFormat('en-GB',{timeZone:TZ,weekday:'short'}).format(new Date()).toLowerCase()==='mon';}
function isWeekend(){const w=new Intl.DateTimeFormat('en-US',{timeZone:TZ,weekday:'short'}).format(new Date()).toLowerCase(); return w==='sat'||w==='sun';}
function weekRangeDates(){const d=new Date();const wd=['sun','mon','tue','wed','thu','fri','sat'].indexOf(new Intl.DateTimeFormat('en-US',{timeZone:TZ,weekday:'short'}).format(d).toLowerCase());const diff=wd===0?-6:1-wd;const mon=new Date(d);mon.setDate(d.getDate()+diff);const sun=new Date(mon);sun.setDate(mon.getDate()+6);return{mon,sun};}
function weekRangeES(){const {mon,sun}=weekRangeDates();return{monday:fmtDateES(mon),sunday:fmtDateES(sun)};}

// ---------- HTTP genérico ----------
async function fetchWithTimeout(url,{timeoutMs=15000,retries=2,method='GET',headers={},body}={}){
  await ensureHTTP();
  let lastErr;
  for(let i=0;i<=retries;i++){
    const ctrl=new _AbortController(); const t=setTimeout(()=>ctrl.abort(new Error('Timeout')),timeoutMs);
    try{
      const res=await _fetch(url,{method,headers,body,signal:ctrl.signal});
      clearTimeout(t);
      if(!res.ok){const txt=await res.text().catch(()=> ''); throw new Error(`HTTP ${res.status} — ${txt.slice(0,120)}`);}
      return res;
    }catch(e){clearTimeout(t); lastErr=e; if(i<retries) await new Promise(r=>setTimeout(r,800*(i+1))); }
  }
  throw lastErr||new Error('fetch failed');
}

// ---------- Investing (widget) ----------
function buildInvestingURL({dateFrom,dateTo,importance,country='5',timeZone='56',lang='12'}){
  const cols='exc_date,exc_time,exc_event,exc_importance';
  const p=new URLSearchParams({country,importance,timeZone,lang,dateFrom,dateTo,columns:cols});
  return `https://ec.forexprostools.com/?${p.toString()}`;
}
function stripTags(s){return String(s).replace(/<[^>]*>/g,'');}
function sanitize(s){return stripTags(s).replace(/\s+/g,' ').trim();}
function parseInvestingHTML(html){
  const rows=[]; const tr=/<tr[^>]*?>([\s\S]*?)<\/tr>/gi; let m;
  while((m=tr.exec(html))!==null){
    const row=m[1];
    const time=sanitize(((row.match(/class="first-time"[^>]*>([\s\S]*?)<\/td>/i)||[])[1])||
                        ((row.match(/data-title="Hora"[^>]*>([\s\S]*?)<\/td>/i)||[])[1])||
                        ((row.match(/class="time"[^>]*>([\s\S]*?)<\/td>/i)||[])[1])||'');
    const date=sanitize(((row.match(/data-title="Fecha"[^>]*>([\s\S]*?)<\/td>/i)||[])[1])||
                        ((row.match(/class="theDay"[^>]*>([\s\S]*?)<\/td>/i)||[])[1])||'');
    const title=sanitize(((row.match(/class="event"[^>]*>([\s\S]*?)<\/td>/i)||[])[1])||
                         ((row.match(/data-title="Evento"[^>]*>([\s\S]*?)<\/td>/i)||[])[1])||'');
    let stars='⭐️⭐️'; if(/bull(3|ish\s*3)|star.?3|alta/i.test(row)) stars='⭐️⭐️⭐️';
    if(!title||!time) continue; rows.push({date,time,title,stars});
  }
  return rows;
}
function normalizeDayES(s){const c=s.charAt(0).toUpperCase()+s.slice(1); return /\d{1,2}\/\d{1,2}/.test(c)?c:c;}
function decorateTitleES(t){const x=t.toLowerCase(); if(/ipc|cpi|inflaci|pce/.test(x)) return '📊 '+t; if(/nfp|no agrícola|payroll|desempleo/.test(x)) return '📊 '+t; if(/fomc|powell|fed/.test(x)) return '🗣️ '+t; return t;}
function groupByDay(rows){const out=[]; let day=null; for(const r of rows){ if(r.date&&r.date.length>=6) day=r.date; if(!day) continue; out.push({dayLabel:normalizeDayES(day),time:r.time,title:decorateTitleES(r.title),stars:r.stars}); } return out; }

// ---------- ForexFactory (fallback JSON) ----------
async function fetchFFWeek(){ const url=`https://nfs.faireconomy.media/ff_calendar_thisweek.json?_=${Date.now()}`; const r=await fetchWithTimeout(url,{headers:{'User-Agent':'Mozilla/5.0','Accept':'application/json'}}); return r.json();}
function impactToStarsFF(impact){return /high/i.test(impact)?'⭐️⭐️⭐️':'⭐️⭐️';}
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
  for(const [rx,es] of map) if(rx.test(s)) return es; return s;
}
function ffToEvents(ff,{fromISO,toISO,impactMin='medium'}){
  const minRank=impactMin==='high'?2:1; const rank=v=>/high/i.test(v)?2:/medium/i.test(v)?1:0;
  const events=[];
  for(const e of ff){
    if((e.country||'').toUpperCase()!=='USD') continue;
    if(rank(e.impact||'')<minRank) continue;
    const ts=Number(e.timestamp)||0; if(!ts) continue;
    const dt=new Date(ts*1000);
    const dISO=fmtDateISO(dt);
    if(dISO<fromISO||dISO>toISO) continue;
    const label=`${weekdayES(dt)} ${fmtDateES(dt)}`;
    events.push({
      dayLabel: label,
      time: fmtTime(dt),
      stars: impactToStarsFF(e.impact||''),
      title: decorateTitleES(translateTitleES(String(e.title||'')))
    });
  }
  // ordenar por día/hora
  events.sort((a,b)=> (a.dayLabel.localeCompare(b.dayLabel)) || (a.time.localeCompare(b.time)));
  return events;
}

// ---------- Mensajes ----------
function limitTelegram(txt){return txt.length>3900?(txt.slice(0,3870)+'\n…recortado'):txt;}
function buildWeeklyMessage(events){
  const {monday,sunday}=weekRangeES();
  const header=`🗓️ Calendario Económico (🇺🇸) — Semana ${monday}–${sunday} (${TZ})\nImpacto: ⭐️⭐️ (medio) · ⭐️⭐️⭐️ (alto)\n`;
  if(!events.length) return `${header}\nNo hay eventos de EE. UU. con el filtro actual.`;
  const map=new Map(); for(const e of events){ if(!map.has(e.dayLabel)) map.set(e.dayLabel,[]); map.get(e.dayLabel).push(e); }
  const lines=[header];
  for(const [day,arr] of map){ lines.push(day); const MAX=5; for(const ev of arr.slice(0,MAX)) lines.push(`• ${ev.time} — ${ev.stars} — ${ev.title}`); if(arr.length>MAX) lines.push(`  +${arr.length-MAX} más…`); lines.push(''); }
  return limitTelegram(lines.join('\n').trim());
}
function buildDailyMessage(events){
  const head=`🗓️ Calendario (🇺🇸) — Hoy ${fmtDateES(new Date())} (${TZ})\nImpacto: ⭐️⭐️ / ⭐️⭐️⭐️\n`;
  if(!events.length) return `${head}\nHoy no hay eventos de EE. UU.`;
  const lines=[head]; for(const ev of events) lines.push(`• ${ev.time} — ${ev.stars} — ${ev.title}`); return limitTelegram(lines.join('\n').trim());
}

// ---------- Telegram ----------
async function sendTelegramText(token,chatId,text){
  await ensureHTTP();
  const url=`https://api.telegram.org/bot${token}/sendMessage`;
  const body=new URLSearchParams({chat_id:chatId,text,parse_mode:'HTML',disable_web_page_preview:'true'});
  const res=await fetchWithTimeout(url,{method:'POST',body,headers:{'Content-Type':'application/x-www-form-urlencoded'}});
  const json=await res.json(); if(!json.ok) throw new Error(`sendMessage Telegram error: ${JSON.stringify(json)}`);
  console.log('Telegram OK');
}

// ---------- Main ----------
(async ()=>{
  if((process.env.BLOCK_WEEKENDS||'').trim()==='1' && isWeekend()){ console.log('Fin de semana → no ejecuto.'); return; }

  const token=process.env.INVESTX_TOKEN, chatId=process.env.CHAT_ID;
  if(!token||!chatId){ console.error('Faltan INVESTX_TOKEN / CHAT_ID'); process.exit(1); }

  const weekly=isMonday();
  const importance=(process.env.IMPACT_MIN||'medium').toLowerCase()==='high'?'3':'2,3';

  // Rango de fechas (normal o forzado)
  let fromISO,toISO;
  if(process.env.FORCE_DATE_FROM && process.env.FORCE_DATE_TO){
    fromISO=process.env.FORCE_DATE_FROM; toISO=process.env.FORCE_DATE_TO;
    console.log(`🔧 Modo prueba → ${fromISO} a ${toISO}`);
  }else if(weekly){ const {mon,sun}=weekRangeDates(); fromISO=fmtDateISO(mon); toISO=fmtDateISO(sun); }
  else { const d=new Date(); fromISO=fmtDateISO(d); toISO=fmtDateISO(d); }

  const SOURCE=(process.env.SOURCE||'auto').toLowerCase();

  // 1) Intento Investing (si auto o investing)
  let events=null, used='none';
  if(SOURCE==='auto' || SOURCE==='investing'){
    try{
      const url=buildInvestingURL({dateFrom:fromISO,dateTo:toISO,importance});
      console.log('Investing URL:', url);
      const res=await fetchWithTimeout(url,{
        timeoutMs:20000,retries:2,
        headers:{
          'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
          'Accept':'text/html,application/xhtml+xml',
          'Accept-Language':'es-ES,es;q=0.9',
          'Referer':'https://es.investing.com/economic-calendar/',
          'Origin':'https://es.investing.com',
          'Cache-Control':'no-cache'
        }
      });
      const html=await res.text();
      if(/403 Forbidden/i.test(html)) throw new Error('Investing devolvió 403 (contenido)');
      const raw=parseInvestingHTML(html);
      console.log(`Investing filas crudas: ${raw.length}`);
      const grouped=groupByDay(raw);
      console.log(`Investing eventos: ${grouped.length}`);
      events=grouped; used='investing';
    }catch(e){
      console.error('Investing falló:', e.message);
      if(SOURCE==='investing'){ throw e; } // si forzaron investing, no seguimos
    }
  }

  // 2) Fallback FF (si auto o ff, y si investing no funcionó)
  if((SOURCE==='auto' || SOURCE==='ff') && !events){
    const ff=await fetchFFWeek();
    const ffEvents=ffToEvents(ff,{fromISO,toISO,impactMin:(process.env.IMPACT_MIN||'medium').toLowerCase()});
    console.log(`FF eventos: ${ffEvents.length}`);
    events=ffEvents; used='ff';
  }

  // Mensaje
  const msg = (process.env.FORCE_DATE_FROM && process.env.FORCE_DATE_TO)
    ? buildWeeklyMessage(events||[])
    : (weekly ? buildWeeklyMessage(events||[]) : buildDailyMessage(events||[]));

  console.log(`Fuente usada: ${used}`);
  await sendTelegramText(token,chatId,msg);
  console.log('Fin OK');
})().catch(e=>{ console.error('ERROR:', e && e.stack ? e.stack : e); process.exit(1); });
