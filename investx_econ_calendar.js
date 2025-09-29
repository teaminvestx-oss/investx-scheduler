/* InvestX Economic Calendar — Render Cron version (puppeteer-core + @sparticuz/chromium)
   Requiere env vars: INVESTX_TOKEN, CHAT_ID
   Runtime: Node 20
*/

const fs = require('fs');
const path = require('path');
const chromium = require('@sparticuz/chromium');
const puppeteer = require('puppeteer-core');

// ---------- Utils de tiempo (Europe/Madrid) ----------
function nowInTZ(tz = 'Europe/Madrid') {
  const d = new Date();
  const fmt = new Intl.DateTimeFormat('sv-SE', {
    timeZone: tz,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit'
  });
  const parts = Object.fromEntries(fmt.formatToParts(d).map(p => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}
function isMonday(tz = 'Europe/Madrid') {
  const d = new Date();
  const wd = new Intl.DateTimeFormat('en-GB', { timeZone: tz, weekday: 'short' }).format(d).toLowerCase();
  return wd === 'mon';
}
function weekRangeISO(tz = 'Europe/Madrid') {
  const d = new Date();
  const wd = ['sun','mon','tue','wed','thu','fri','sat']
    .indexOf(new Intl.DateTimeFormat('en-US',{timeZone:tz,weekday:'short'}).format(d).toLowerCase());
  const diffToMon = wd === 0 ? -6 : 1 - wd;
  const monday = new Date(d);
  monday.setDate(d.getDate() + diffToMon);
  const sunday = new Date(monday);
  sunday.setDate(monday.getDate() + 6);

  const f = (x) => new Intl.DateTimeFormat('sv-SE',{timeZone:tz,dateStyle:'short'}).format(x);
  return { monday: f(monday), sunday: f(sunday) };
}

// ---------- Helpers DOM ----------
async function wait(ms){ return new Promise(r=>setTimeout(r,ms)); }

async function clickByText(page, selector, texts, timeoutMs = 8000) {
  const end = Date.now() + timeoutMs;
  const wants = texts.map(t => t.toLowerCase());
  while (Date.now() < end) {
    const ok = await page.evaluate(({ selector, wants }) => {
      const candidates = document.querySelectorAll(selector);
      for (const el of candidates) {
        const t = ((el.innerText || el.textContent || el.value || '') + '').toLowerCase().trim();
        if (wants.some(w => t.includes(w))) {
          el.click();
          return true;
        }
      }
      return false;
    }, { selector, wants });
    if (ok) return true;
    await wait(250);
  }
  return false;
}

async function applyFilters(page) {
  await clickByText(page, 'button,a,[role="button"],input[type="button"]', ['filtro','filtros','filters'], 8000).catch(()=>{});
  await wait(600);
  await page.evaluate(() => {
    const root = document.querySelector('.filterPopup,[class*="filterPop"]') || document;
    const countryCbs = [...root.querySelectorAll('input[type="checkbox"][name*="country"]')];
    countryCbs.forEach(cb => { cb.checked = false; cb.dispatchEvent(new Event('change', { bubbles: true })); });
    let us = countryCbs.find(cb => cb.value === '5') ||
             countryCbs.find(cb => /estados unidos|united states|ee\.uu/i.test((cb.closest('label,li,div')?.innerText||'')));
    if (us) { us.checked = true; us.dispatchEvent(new Event('change', { bubbles: true })); }

    const impCbs = [...root.querySelectorAll('input[type="checkbox"][name*="importance"]')];
    impCbs.forEach(cb => {
      cb.checked = (cb.value === '2' || cb.value === '3');
      cb.dispatchEvent(new Event('change', { bubbles: true }));
    });

    const btn = [...root.querySelectorAll('button,a,input[type="submit"],input[type="button"]')]
      .find(b => /aplicar|apply|mostrar|show/i.test((b.innerText || b.value || '')));
    btn?.click();
  });
  await wait(1200);
}

// ---------- Scraper principal ----------
async function buildCalendar() {
  const browser = await puppeteer.launch({
    headless: true,
    executablePath: await chromium.executablePath(),
    args: [...chromium.args, '--lang=es-ES,es', '--no-sandbox', '--disable-setuid-sandbox'],
    defaultViewport: { width: 1440, height: 2400, deviceScaleFactor: 2 }
  });
  const page = await browser.newPage();
  await page.setUserAgent(
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36'
  );
  await page.setExtraHTTPHeaders({'Accept-Language':'es-ES,es;q=0.9'});

  await page.goto('https://es.investing.com/economic-calendar/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.evaluate(() => {
    const b = [...document.querySelectorAll("button,a,[role='button']")]
      .find(x => /aceptar|accept|consent|agree/i.test((x.innerText || '')));
    b?.click();
  }).catch(()=>{});
  await wait(600);

  if (isMonday()) {
    await clickByText(page, 'a,button', ['esta semana','this week'], 6000);
  } else {
    await clickByText(page, 'a,button', ['hoy','today'], 6000);
  }
  await wait(500);
  await applyFilters(page);
  await wait(600);

  const data = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('tr[id^="eventRowId_"],tr.js-event-item,tr[data-event-datetime]')];

    const cleanTitle = (tr) => {
      const pick = (...xs) => xs.find(v => v && v.trim && v.trim().length > 0);
      const byAttr = tr.getAttribute('data-event-title');
      const aTitle = tr.querySelector('td[class*="event"] a[title]')?.getAttribute('title');
      const aria   = tr.querySelector('td[class*="event"] [aria-label]')?.getAttribute('aria-label');
      const txt1   = tr.querySelector('td[class*="event"] a')?.textContent;
      const txt2   = tr.querySelector('td[class*="event"], td.left, td:nth-child(3)')?.textContent;
      let raw = (pick(byAttr, aTitle, aria, txt1, txt2) || '').replace(/\s+/g, ' ').trim();
      if (/^\d{1,2}:\d{2}$/.test(raw) || raw.length < 4) raw = '';
      return raw;
    };

    const events = [];
    for (const tr of rows) {
      if (tr.style.display === 'none') continue;
      const tds = tr.querySelectorAll('td');
      const time = (tds[0]?.innerText || '').trim();
      const title = cleanTitle(tr);
      if (!title) continue;

      let imp = parseInt(tr.getAttribute('data-importance') || '0', 10);
      if (!imp || isNaN(imp)) {
        const s = tr.querySelector('td.sentiment,td.impact,.sentiment');
        if (s) {
          const n = s.querySelectorAll('i,svg').length;
          imp = n >= 3 ? 3 : (n >= 2 ? 2 : 1);
        } else { imp = 0; }
      }

      events.push({
        time,
        title,
        importance: imp,
        iso: tr.getAttribute('data-event-datetime') || '',
        forecast: (tr.querySelector('td.fore,td.forecast')?.innerText || '').trim(),
        previous: (tr.querySelector('td.prev,td.previous')?.innerText || '').trim()
      });
    }
    return { events };
  });

  const table = await page.$('#economicCalendarData') || await page.$('table.genTbl');
  if (table) {
    await table.screenshot({ path: 'calendar.png' });
  }

  fs.writeFileSync('calendar.json', JSON.stringify(data, null, 2));
  await browser.close();

  return data;
}

// ---------- Normalización texto ----------
function normalize(s) {
  return (s || '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
}

// ---------- Build summary ----------
function buildSummary(events) {
  const FED_NAMES = ['powell','waller','jefferson','cook','bowman','williams','bostic',
                     'kashkari','daly','goolsbee','barkin','logan','mester','harker'];
  const POLITICIANS = ['trump','biden','harris'];

  const data_cpi = [], data_pce = [], data_nfp = [], data_gdp = [], data_pmi = [], data_ca = [];
  const fed_powell = [], fed_members = [], politics = [];

  for (const e of events) {
    const t = (e.title || '').trim();
    const tl = normalize(t);

    const isSpeech = ['comparecencia','declaraciones','discurso','habla'].some(k => tl.includes(k));
    const isPolit  = POLITICIANS.some(k => tl.includes(k));
    if (isPolit) { politics.push(e); continue; }

    if (isSpeech || FED_NAMES.some(n => tl.includes(n))) {
      if (tl.includes('powell')) fed_powell.push(e);
      else fed_members.push(e);
      continue;
    }
    if (tl.includes('ipc') || tl.includes('cpi')) { data_cpi.push(e); continue; }
    if (tl.includes('pce')) { data_pce.push(e); continue; }
    if (['nóminas','nominas','nfp','empleo','desempleo'].some(k => tl.includes(k))) { data_nfp.push(e); continue; }
    if (tl.includes('pib') || tl.includes('gdp')) { data_gdp.push(e); continue; }
    if (tl.includes('pmi') || tl.includes('ism')) { data_pmi.push(e); continue; }
    if (tl.includes('cuenta corriente') || tl.includes('balanza por cuenta corriente')) { data_ca.push(e); continue; }
  }

  const pickTimeRange = (lst) => {
    const times = lst.map(x => x.time).filter(Boolean).sort();
    if (!times.length) return '';
    const a = times[0], b = times[times.length - 1];
    return (a === b) ? a : `${a}–${b}`;
  };

  const paras = [];

  if (data_cpi.length) {
    const e = data_cpi[0];
    const meta = [];
    if (e.forecast) meta.push(`consenso ${e.forecast}`);
    if (e.previous) meta.push(`anterior ${e.previous}`);
    const extra = meta.length ? ` — ${meta.join(', ')}` : '';
    paras.push(
      `📌 <b>IPC USA (${e.time || '--:--'})</b>\n` +
      `La referencia clave de inflación mensual. El mercado vigilará subyacente. ${extra}`
    );
  }
  if (data_pce.length) {
    const e = data_pce[0];
    paras.push(`📌 <b>PCE subyacente (${e.time || '--:--'})</b>\nIndicador preferido por la Fed.`);
  }
  if (data_nfp.length) {
    const e = data_nfp[0];
    paras.push(`📌 <b>Nóminas no agrícolas – NFP (${e.time || '--:--'})</b>\nClaves: creación de empleo y salarios.`);
  }
  if (data_gdp.length) {
    const e = data_gdp[0];
    paras.push(`📌 <b>PIB (GDP) (${e.time || '--:--'})</b>\nPulso de la actividad económica.`);
  }
  if (data_pmi.length) {
    const hh = pickTimeRange(data_pmi) || '--:--';
    paras.push(`📌 <b>PMI/ISM (${hh})</b>\nTermómetro adelantado del ciclo.`);
  }
  if (fed_powell.length) {
    const hh = pickTimeRange(fed_powell) || '--:--';
    paras.push(`📌 <b>Powell (${hh})</b>\nAtentos al tono del presidente de la Fed.`);
  }

  const limit = isMonday() ? 4 : 3;
  const sel = paras.slice(0, limit);
  if (!sel.length) return '';

  return "📰 <b>Resumen principales noticias</b>\n\n" + sel.join("\n\n");
}

// ---------- Telegram ----------
async function sendTelegramPhoto(token, chatId, caption, filePath) {
  const url = `https://api.telegram.org/bot${token}/sendPhoto`;
  const form = new FormData();
  form.append('chat_id', chatId);
  form.append('caption', caption);
  form.append('parse_mode', 'HTML');
  form.append('photo', new Blob([fs.readFileSync(filePath)]), path.basename(filePath));
  const res = await fetch(url, { method: 'POST', body: form });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(`sendPhoto failed: ${res.status} ${t}`);
  }
}

async function sendTelegramText(token, chatId, htmlText) {
  const url = `https://api.telegram.org/bot${token}/sendMessage`;
  const params = new URLSearchParams();
  params.set('chat_id', chatId);
  params.set('text', htmlText);
  params.set('parse_mode', 'HTML');
  params.set('disable_web_page_preview', 'true');
  const res = await fetch(url, { method: 'POST', body: params });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(`sendMessage failed: ${res.status} ${t}`);
  }
}

// ---------- Main ----------
(async () => {
  const token = process.env.INVESTX_TOKEN;
  const chatId = process.env.CHAT_ID;
  if (!token || !chatId) {
    console.error('Faltan variables de entorno INVESTX_TOKEN y/o CHAT_ID');
    process.exit(1);
  }

  console.log(`[${nowInTZ()}] Iniciando scrape…`);
  const { events = [] } = await buildCalendar();

  const pngExists = fs.existsSync('calendar.png');
  const todayISO = new Intl.DateTimeFormat('sv-SE', { timeZone:'Europe/Madrid', dateStyle: 'short' }).format(new Date());
  const { monday, sunday } = weekRangeISO('Europe/Madrid');

  const caption = isMonday()
    ? `🗓️ Calendario USA (⭐️⭐️/⭐️⭐️⭐️) — Semana ${monday}–${sunday}`
    : `🗓️ Calendario USA (⭐️⭐️/⭐️⭐️⭐️) — Hoy ${todayISO}`;

  if (pngExists) {
    console.log('Enviando imagen a Telegram…');
    await sendTelegramPhoto(token, chatId, caption, 'calendar.png');
  } else {
    console.log('No se generó calendar.png');
  }

  const summary = buildSummary(events);
  if (summary) {
    console.log('Enviando resumen a Telegram…');
    await sendTelegramText(token, chatId, summary);
  } else {
    console.log('Sin resumen relevante.');
  }

  console.log('Hecho.');
})().catch(err => {
  console.error('ERROR:', err);
  process.exit(1);
});
