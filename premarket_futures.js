// Node 20 + Puppeteer
// Toma % de ES, NQ, YM, RTY en Finviz Futuros, crea imagen y la envía a Telegram

import fs from "fs/promises";
import path from "path";
import puppeteer from "puppeteer";

const BOT_TOKEN = process.env.INVESTX_TOKEN?.trim();
const CHAT_ID   = process.env.CHAT_ID?.trim();
const TZ        = "Europe/Madrid";

// ---- utilidades
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const nowLocal = () => new Date(new Date().toLocaleString("en-US", { timeZone: TZ }));

function inWindow12Local(d = nowLocal()) {
  const h = d.getHours(), m = d.getMinutes();
  // ventana 11:55–12:15 para evitar fallos de arranque del runner
  const mins = h * 60 + m;
  return mins >= (11*60+55) && mins <= (12*60+15);
}

function parsePct(txt) {
  // "0.45%" -> 0.45   |  "-1.23%" -> -1.23
  if (!txt) return null;
  const m = txt.replace(",", ".").match(/-?\d+(\.\d+)?/);
  return m ? parseFloat(m[0]) : null;
}

function tone(mean){
  if (mean > 0.2) return "🟢 Sesgo alcista";
  if (mean < -0.2) return "🔴 Sesgo bajista";
  return "⚪ Sesgo neutral";
}

async function fetchFinvizFutures() {
  const url = "https://finviz.com/futures.ashx";
  const browser = await puppeteer.launch({
    headless: "new",
    args: ["--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage","--lang=es-ES,es"]
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1400, height: 1000, deviceScaleFactor: 2 });
  await page.setUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36");

  // carga y acepta cookies si aparecen
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 }).catch(()=>{});
  await page.evaluate(() => {
    const btn = [...document.querySelectorAll("button,a")].find(x => /accept|agree|consent|aceptar/i.test(x?.innerText||""));
    btn?.click();
  }).catch(()=>{});
  await sleep(1200);

  // Extrae tarjetas de índices: ES, NQ, YM, RTY
  const data = await page.evaluate(() => {
    // Buscar tarjetas con símbolo visible
    const readCard = (symWanted) => {
      const nodes = [...document.querySelectorAll(".futures-cont .screener-body-table tr, .futures .table tr, [class*='content'] .table tr")];
      // alternativa: tarjetas nuevas con data-symbol
      const blocks = [...document.querySelectorAll("[data-ticker], [data-symbol]")];
      const pools = blocks.length ? blocks : nodes;

      let best = null;
      for (const n of pools) {
        const txt = (n.innerText||"").replace(/\s+/g," ").trim();
        if (!txt) continue;
        if (new RegExp(`\\b${symWanted}\\b`, "i").test(txt)) {
          const pctMatch = txt.match(/-?\d+(?:[.,]\d+)?\s*%/);
          const lastMatch = txt.match(/\b\d{2,5}(?:[.,]\d+)?\b/);
          best = {
            sym: symWanted,
            pctText: pctMatch ? pctMatch[0] : null,
            lastText: lastMatch ? lastMatch[0] : null
          };
          break;
        }
      }
      return best;
    };

    const targets = [
      {name:"S&P 500", sym:"ES"},
      {name:"Nasdaq 100", sym:"NQ"},
      {name:"Dow Jones", sym:"YM"},
      {name:"Russell 2000", sym:"RTY"},
    ];

    const out = [];
    for (const t of targets) {
      const r = readCard(t.sym);
      out.push({
        name: t.name,
        sym: t.sym,
        pctText: r?.pctText || null,
        lastText: r?.lastText || null
      });
    }
    return out;
  });

  // Crea una mini-página para render bonito y capturar screenshot
  const html = (rows) => {
    const ts = nowLocal().toLocaleString("es-ES", { timeZone: TZ, hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short", year: "numeric" });
    const card = (r) => {
      const pct = r.pctText || "—";
      const val = r.lastText || "—";
      const num = parseFloat((pct.match(/-?\d+(?:\.\d+)?/)||[""])[0] || "NaN");
      const color = isNaN(num) ? "#A0A0A0" : (num>=0 ? "#22a65e" : "#d64541");
      return `
        <div class="card">
          <div class="title">${r.name}</div>
          <div class="pct" style="color:${color}">${pct}</div>
          <div class="sub">Último: ${val}</div>
        </div>`;
    };
    return `
      <html><head><meta charset="utf-8"/>
      <style>
        @font-face { font-family: Inter; src: local("Arial"); font-weight: 400; }
        body{ margin:0; background:#0c0c0c; color:#eee; font-family: Inter, Arial, sans-serif; }
        .wrap{ width:1200px; height:630px; padding:40px; box-sizing:border-box; }
        h1{ font-size:44px; margin:0 0 8px 0; }
        .muted{ color:#b4b4b4; font-size:18px; margin-bottom:24px; }
        .grid{ display:grid; grid-template-columns:1fr 1fr; grid-gap:28px; }
        .card{ background:#16161a; border-radius:18px; padding:24px; }
        .title{ font-size:24px; font-weight:700; margin-bottom:8px; }
        .pct{ font-size:48px; font-weight:800; margin-top:10px; }
        .sub{ font-size:18px; color:#b4b4b4; margin-top:8px; }
      </style></head>
      <body>
        <div class="wrap">
          <h1>Premarket US — Futuros</h1>
          <div class="muted">Actualizado: ${ts} (${TZ})</div>
          <div class="grid">
            ${rows.map(card).join("")}
          </div>
        </div>
      </body></html>`;
  };

  const vals = data.map(r => ({...r, pct: parsePct(r.pctText)}));
  const mean = vals.filter(v => typeof v.pct === "number" && !Number.isNaN(v.pct))
                   .reduce((a,b)=>a+b.pct,0) / Math.max(1, vals.filter(v=>!Number.isNaN(v.pct)).length);
  const best = vals.reduce((a,b)=> (a.pct??-1e9) > (b.pct??-1e9) ? a : b);
  const worst= vals.reduce((a,b)=> (a.pct??+1e9) < (b.pct??+1e9) ? a : b);
  const spreadOk = (Number.isFinite(best.pct) && Number.isFinite(worst.pct)) ? Math.abs(best.pct - worst.pct) : null;

  const interpretation = (() => {
    const lines = [];
    const t = tone(mean);
    const mtxt = Number.isFinite(mean) ? mean.toFixed(2) : "—";
    lines.push(`${t} en futuros: media ${Number.isFinite(mean)? (mean>=0?"+":"") + mtxt + "%" : "—"}.`);
    if (Number.isFinite(best.pct) && Number.isFinite(worst.pct)) {
      lines.push(`Mejor: ${best.name} ${(best.pct>=0?"+":"")}${best.pct.toFixed(2)}% | Peor: ${worst.name} ${(worst.pct>=0?"+":"")}${worst.pct.toFixed(2)}%.`);
    }
    if (spreadOk !== null) {
      lines.push(spreadOk >= 0.6 ? "Rotación marcada entre índices." : "Movimiento relativamente homogéneo.");
    }
    lines.push("Apertura sujeta a titulares macro/empresa; vigilar niveles iniciales.");
    return lines.join(" ");
  })();

  // Render imagen bonita
  const tmp = await browser.newPage();
  await tmp.setViewport({ width: 1200, height: 630, deviceScaleFactor: 2 });
  await tmp.setContent(html(vals), { waitUntil: "networkidle0" });
  await tmp.screenshot({ path: "premarket.png" });
  await tmp.close();

  // Construye caption
  const lines = ["<b>Premarket USA</b>"];
  for (const r of vals) {
    const p = Number.isFinite(r.pct) ? `${r.pct>=0?"+":""}${r.pct.toFixed(2)}%` : "—";
    lines.push(`• <b>${r.name}</b>: ${p}`);
  }
  lines.push("");
  lines.push(interpretation);
  const caption = lines.join("\n");

  // Enviar a Telegram (solo si estamos en ventana o si es ejecución manual)
  const event = process.env.GITHUB_EVENT_NAME || "";
  const force = process.env.FORCE_SEND === "1";
  const allow = inWindow12Local() || event === "workflow_dispatch" || force;

  if (!allow) {
    console.log(`[guard] Fuera de ventana 12:00 ${TZ}. No envío.`);
  } else {
    if (!BOT_TOKEN || !CHAT_ID) throw new Error("Faltan INVESTX_TOKEN o CHAT_ID");
    const url = `https://api.telegram.org/bot${BOT_TOKEN}/sendPhoto`;
    const photo = await fs.readFile("premarket.png");
    const form = new FormData();
    form.append("chat_id", CHAT_ID);
    form.append("caption", caption);
    form.append("parse_mode", "HTML");
    form.append("photo", new Blob([photo], { type: "image/png" }), "premarket.png");
    const res = await fetch(url, { method: "POST", body: form });
    const txt = await res.text();
    console.log(res.status, txt);
    if (!res.ok) throw new Error(txt);
  }

  await browser.close();
}

(async () => {
  try {
    // si se lanza manualmente siempre envía
    await fetchFinvizFutures();
  } catch (e) {
    console.error("[error]", e);
    process.exit(1);
  }
})();
