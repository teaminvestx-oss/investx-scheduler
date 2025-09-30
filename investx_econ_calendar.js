/* InvestX Economic Calendar — SIN Puppeteer (robusto en Render)
   Requiere env: INVESTX_TOKEN, CHAT_ID
*/

const fs = require('fs');
const path = require('path');
const cheerio = require('cheerio');
const PImage = require('pureimage');

// ---------- Tiempo (Europe/Madrid) ----------
function isMonday(tz='Europe/Madrid'){return new Intl.DateTimeFormat('en-GB',{timeZone:tz,weekday:'short'}).format(new Date()).toLowerCase()==='mon'}
function todayISO(tz='Europe/Madrid'){return new Intl.DateTimeFormat('sv-SE',{timeZone:tz,dateStyle:'short'}).format(new Date())}
function weekRangeISO(tz='Europe/Madrid'){
  const d=new Date();
  const wd=['sun','mon','tue','wed','thu','fri','sat'].indexOf(new Intl.DateTimeFormat('en-US',{timeZone:tz,weekday:'short'}).format(d).toLowerCase());
  const diff=wd===0?-6:1-wd; const mon=new Date(d); mon.setDate(d.getDate()+diff); const sun=new Date(mon); sun.setDate(mon.getDate()+6);
  const f=x=>new Intl.DateTimeFormat('sv-SE',{timeZone:tz,dateStyle:'short'}).format(x); return {monday:f(mon), sunday:f(sun)}
}
function nowInTZ(tz='Europe/Madrid'){const d=new Date();const f=new Intl.DateTimeFormat('sv-SE',{timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});const p=Object.fromEntries(f.formatToParts(d).map(x=>[x.type,x.value]));return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`}

// ---------- Descarga widget (con Referer/Origin) ----------
async function fetchCalendar(calType){
  // hosts espejo del widget
  const hosts = ['ec.forexprostools.com', 'sslecal2.forexprostools.com'];
  const qs = `columns=exc,cur,event,act,for,pre&importance=2,3&countries=5&calType=${calType}&timeZone=56&_=${Date.now()}`;
  const headers = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36',
    'Accept': 'text/html, */*; q=0.01',
    'Accept-Language': 'es-ES,es;q=0.9',
    'Referer': 'https://www.investing.com/economic-calendar/',
    'Origin': 'https://www.investing.com',
    'X-Requested-With': 'XMLHttpRequest'
  };

  let lastErr = null;
  for (const host of hosts) {
    const url = `https://${host}/?${qs}`;
    try {
      const res = await fetch(url, { headers, redirect: 'follow' });
      if (!res.ok) { lastErr = new Error(`Fetch calendar failed (${host}): ${res.status}`); continue; }
      const html = await res.text();
      return parseCalendar(html);
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr || new Error('All widget hosts failed');
}

function parseCalendar(html){
  const $=cheerio.load(html);
  const rows=$('tr[id^="eventRowId_"],tr.js-event-item,tr[data-event-datetime]');
  const events=[];
  rows.each((_,tr)=>{
    const $tr=$(tr);
    const time=($tr.find('td').first().text()||'').trim();

    let imp=0; const $sent=$tr.find('td.sentiment,td.impact,.sentiment');
    if($sent.length){const n=$sent.find('i,svg,span').length; imp=n>=3?3:n>=2?2:n>=1?1:0;}

    const title=(
      $tr.attr('data-event-title') ||
      $tr.find('td.event, td.left, td:nth-child(3)').text() ||
      $tr.text()
    ).replace(/\s+/g,' ').trim();
    if(!title || title.length<4) return;

    const forecast=($tr.find('td.fore, td.forecast').text()||'').trim();
    const previous=($tr.find('td.prev, td.previous').text()||'').trim();
    const iso=$tr.attr('data-event-datetime')||'';

    if(imp>=2) events.push({ time, title, importance: imp, forecast, previous, iso });
  });
  return { events };
}

// ---------- PNG simple ----------
async function drawPNG(events, caption){
  try{
    const width=1200,rowH=56,headerH=100,h=headerH+rowH*Math.min(events.length,18)+40;
    const img=PImage.make(width,h); const ctx=img.getContext('2d');
    ctx.fillStyle='#fff'; ctx.fillRect(0,0,width,h);
    const font='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'; if(fs.existsSync(font)){const f=PImage.registerFont(font,'UI'); await f.load();}
    ctx.fillStyle='#111'; ctx.font='32pt UI, Arial'; ctx.fillText('Calendario económico USA (⭐️⭐️/⭐️⭐️⭐️)',28,56);
    ctx.font='18pt UI, Arial'; ctx.fillStyle='#444'; ctx.fillText(caption,28,86);
    ctx.fillStyle='#222'; ctx.font='16pt UI, Arial'; ctx.fillText('Hora',28,headerH); ctx.fillText('Evento',150,headerH); ctx.fillText('Forecast',900,headerH); ctx.fillText('Previo',1040,headerH);
    ctx.strokeStyle='#e5e7eb'; ctx.beginPath(); ctx.moveTo(20,headerH+10); ctx.lineTo(width-20,headerH+10); ctx.stroke();
    ctx.font='15pt UI, Arial'; let y=headerH+40;
    for(const e of events.slice(0,18)){
      ctx.fillStyle='#111'; ctx.fillText(e.time||'--:--',28,y);
      const maxW=720; let t=e.title||''; while(t.length && ctx.measureText(t+'…').width>maxW) t=t.slice(0,-1); if(t!==e.title) t+='…';
      ctx.fillText(t,150,y);
      ctx.fillStyle='#2563eb'; ctx.fillText(e.forecast||'-',900,y);
      ctx.fillStyle='#6b7280'; ctx.fillText(e.previous||'-',1040,y);
      ctx.strokeStyle='#f3f4f6'; ctx.beginPath(); ctx.moveTo(20,y+14); ctx.lineTo(width-20,y+14); ctx.stroke();
      y+=rowH;
    }
    const out=fs.createWriteStream('calendar.png');
    await PImage.encodePNGToStream(img,out); await new Promise(r=>out.on('finish',r));
    return true;
  }catch(e){ console.error('PNG generation failed:', e.message); return false; }
}

// ---------- Resumen ----------
function buildSummary(events){
  if(!events||!events.length) return '';
  const top=[...events].sort((a,b)=>(b.importance||0)-(a.importance||0)).slice(0,3);
  const blocks=top.map(e=>{
    const meta=[]; if(e.forecast) meta.push(`consenso ${e.forecast}`); if(e.previous) meta.push(`anterior ${e.previous}`);
    const extra=meta.length?` — ${meta.join(', ')}`:''; return `📌 <b>${e.title}</b> (${e.time||'--:--'})${extra}`;
  });
  return "📰 <b>Resumen principales noticias</b>\n\n"+blocks.join("\n\n");
}

// ---------- Telegram ----------
async function sendTelegramPhoto(token,chatId,caption,filePath){
  const url=`https://api.telegram.org/bot${token}/sendPhoto`;
  const form=new FormData();
  form.append('chat_id',chatId); form.append('caption',caption); form.append('parse_mode','HTML');
  form.append('photo', new Blob([fs.readFileSync(filePath)]), path.basename(filePath));
  const r=await fetch(url,{method:'POST',body:form}); if(!r.ok) throw new Error(`sendPhoto failed: ${r.status} ${await r.text()}`);
}
async function sendTelegramText(token,chatId,html){
  const url=`https://api.telegram.org/bot${token}/sendMessage`;
  const body=new URLSearchParams({chat_id:chatId,text:html,parse_mode:'HTML',disable_web_page_preview:'true'});
  const r=await fetch(url,{method:'POST',body}); if(!r.ok) throw new Error(`sendMessage failed: ${r.status} ${await r.text()}`);
}

// ---------- Main ----------
(async ()=>{
  const token=process.env.INVESTX_TOKEN, chatId=process.env.CHAT_ID;
  if(!token||!chatId){ console.error('Faltan INVESTX_TOKEN / CHAT_ID'); process.exit(1); }

  console.log(`[${nowInTZ()}] Descargando calendario…`);
  const mode=isMonday()?'week':'day';
  const {events=[]}=await fetchCalendar(mode);

  const {monday,sunday}=weekRangeISO();
  const caption=isMonday()?`🗓️ Calendario USA (⭐️⭐️/⭐️⭐️⭐️) — Semana ${monday}–${sunday}`:`🗓️ Calendario USA (⭐️⭐️/⭐️⭐️⭐️) — Hoy ${todayISO()}`;

  let sent=false;
  if(events.length){
    const ok=await drawPNG(events,caption);
    if(ok && fs.existsSync('calendar.png')){ console.log('Enviando imagen…'); await sendTelegramPhoto(token,chatId,caption,'calendar.png'); sent=true; }
  } else {
    console.log('Sin eventos del widget (lista vacía).');
  }

  const summary=buildSummary(events);
  if(summary){ console.log('Enviando resumen…'); await sendTelegramText(token,chatId,summary); }
  else if(!sent){ await sendTelegramText(token,chatId,`🗓️ ${caption}\n\n(No hay eventos relevantes hoy).`); }

  console.log('Hecho.');
})().catch(err=>{ console.error('ERROR:',err); process.exit(1); });
