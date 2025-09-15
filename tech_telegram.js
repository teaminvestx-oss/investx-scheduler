// tech_telegram.js
// InvestX: mapa SOLO del sector Tecnología (ampliado) + tabla filtrada (ampliada) de Finviz.
// Evita pantallas de Cloudflare con puppeteer-extra+stealth y hace zoom + recorte nítido.

const fs = require("fs");
const path = require("path");
const dayjs = require("dayjs");

const BOT_TOKEN = process.env.BOT_TOKEN;
const CHAT_ID = process.env.CHAT_ID;
if (!BOT_TOKEN || !CHAT_ID) {
  console.error("Faltan BOT_TOKEN o CHAT_ID");
  process.exit(1);
}

const puppeteer = require("puppeteer-extra");
const StealthPlugin = require("puppeteer-extra-plugin-stealth");
puppeteer.use(StealthPlugin());

// ---- Telegram helpers ----
async function sendPhoto(filepath, caption = "") {
  const fetch = (await import("node-fetch")).default;
  const FormData = (await import("form-data")).default;
  const url = `https://api.telegram.org/bot${BOT_TOKEN}/sendPhoto`;
  const fd = new FormData();
  fd.append("chat_id", CHAT_ID);
  if (caption) fd.append("caption", caption);
  fd.append("parse_mode", "HTML");
  fd.append("photo", fs.createReadStream(filepath));
  const res = await fetch(url, { method: "POST", body: fd });
  if (!res.ok) console.error("sendPhoto error:", await res.text());
}

async function sendMessage(text) {
  const fetch = (await import("node-fetch")).default;
  const url = `https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: CHAT_ID, text, parse_mode: "HTML", disable_web_page_preview: true }),
  });
  if (!res.ok) console.error("sendMessage error:", await res.text());
}

(async () => {
  const outDir = path.join(process.cwd(), "out");
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir);
  const ts = dayjs().format("YYYYMMDD");
  const mapPng = path.join(outDir, `tech_map_${ts}.png`);
  const tablePng = path.join(outDir, `tech_table_${ts}.png`);

  const browser = await puppeteer.launch({
    headless: "new",
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--window-size=1920,1400",
      "--lang=en-US,en",
    ],
    defaultViewport: { width: 1920, height: 1400, deviceScaleFactor: 2 }, // alta definición
  });
  const page = await browser.newPage();
  await page.setUserAgent(
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
  );
  await page.setExtraHTTPHeaders({ "accept-language": "en-US,en;q=0.9,es;q=0.8" });

  // --- 1) Cargar mapa con reintentos (Cloudflare) ---
  const FINVIZ_MAP = "https://finviz.com/map.ashx";
  async function loadMapWithRetry(tries = 6) {
    for (let i = 1; i <= tries; i++) {
      try {
        await page.goto(FINVIZ_MAP, { waitUntil: "domcontentloaded", timeout: 60000 });
        const isCF = await page.evaluate(() =>
          /review the security of your connection|verifying you are human|checking your browser/i.test(
            document.body.innerText || ""
          )
        );
        if (isCF) { await page.waitForTimeout(8000); continue; }
        await page.waitForSelector("#map", { timeout: 60000 });
        return true;
      } catch {
        await page.waitForTimeout(5000);
      }
    }
    return false;
  }
  const ok = await loadMapWithRetry();
  if (!ok) {
    await page.screenshot({ path: mapPng, fullPage: true });
    await sendPhoto(mapPng, "⚠️ No se pudo cargar Finviz (Cloudflare).");
    await sendMessage("No se pudo cargar Finviz tras varios intentos. Enviada captura de diagnóstico.");
    await browser.close();
    process.exit(0);
  }

  // --- 2) LOCALIZAR y ZOOM al bloque TECHNOLOGY (recorte grande, legible) ---
  // 2.1 localizar rectángulo del sector dentro del mapa
  let techRect = await page.evaluate(() => {
    function rect(el){ const r = el.getBoundingClientRect(); return {x:r.x, y:r.y, w:r.width, h:r.height}; }
    const map = document.querySelector("#map") || document.body;
    const blocks = [...map.querySelectorAll("div")].filter(d => (d.innerText||"").trim());
    let best = null;
    for (const el of blocks) {
      const t = (el.innerText||"").replace(/\s+/g," ").toUpperCase();
      if (t.includes("TECHNOLOGY")) {
        const r = rect(el);
        if (r.w > 300 && r.h > 200) { best = r; break; }
      }
    }
    return best;
  });

  // 2.2 si lo tenemos, hacemos "zoom CSS" para que ese rect ancho ~ 1400px
  if (techRect) {
    const targetWidth = 1400;                         // tamaño final cómodo para móvil
    const scale = Math.min(2.8, Math.max(1.2, targetWidth / techRect.w)); // limita el zoom
    await page.evaluate((s) => { document.body.style.zoom = String(s); }, scale);
    await page.waitForTimeout(300);

    // Recalcular rect ya escalado y centrarlo en viewport
    techRect = await page.evaluate(() => {
      function rect(el){ const r = el.getBoundingClientRect(); return {x:r.x, y:r.y, w:r.width, h:r.height}; }
      const map = document.querySelector("#map") || document.body;
      const blocks = [...map.querySelectorAll("div")];
      for (const el of blocks) {
        const t = (el.innerText||"").toUpperCase();
        if (t.includes("TECHNOLOGY")) {
          const r = rect(el);
          if (r.w > 300 && r.h > 200) return r;
        }
      }
      return null;
    });

    if (techRect) {
      // centrar scroll
      await page.evaluate(({x,y,w,h}) => {
        window.scrollTo({ left: Math.max(0, x + w/2 - window.innerWidth/2), top: Math.max(0, y + h/2 - window.innerHeight/2) });
      }, techRect);
      await page.waitForTimeout(200);

      // añadir padding alrededor para que no quede “cortado”
      const pad = 20;
      const clip = {
        x: Math.max(0, techRect.x - pad),
        y: Math.max(0, techRect.y - pad),
        width: Math.min(techRect.w + pad*2, await page.evaluate(() => document.body.getBoundingClientRect().width)),
        height: Math.min(techRect.h + pad*2, await page.evaluate(() => document.body.getBoundingClientRect().height)),
      };
      await page.screenshot({ path: mapPng, clip });
    } else {
      // fallback: full page
      await page.screenshot({ path: mapPng, fullPage: true });
    }
  } else {
    // fallback si no encontramos el bloque
    const mapEl = await page.$("#map");
    if (mapEl) {
      const bb = await mapEl.boundingBox();
      await page.screenshot({ path: mapPng, clip: bb });
    } else {
      await page.screenshot({ path: mapPng, fullPage: true });
    }
  }

  // --- 3) TABLA (Screener Tecnología) y captura grande ---
  const FINVIZ_SCREENER_TECH = "https://finviz.com/screener.ashx?v=111&f=sec_technology";
  await page.goto(FINVIZ_SCREENER_TECH, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForSelector("#screener-content", { timeout: 60000 }).catch(()=>{});
  // pequeño zoom para que el texto se lea mejor
  await page.evaluate(() => { document.body.style.zoom = "1.25"; });
  await page.waitForTimeout(150);

  // capturar el bloque del screener (limpio)
  const tbl = await page.$("#screener-content");
  if (tbl) {
    const bb = await tbl.boundingBox();
    await page.screenshot({ path: tablePng, clip: bb });
  }

  // extraer primeras 15 filas para texto
  const rows = await page.evaluate(() => {
    const out = [];
    const table = document.querySelector("#screener-content table.table-light");
    if (!table) return out;
    const trs = table.querySelectorAll("tr[valign='top']");
    for (const tr of trs) {
      const tds = tr.querySelectorAll("td");
      if (tds.length < 3) continue;
      const sym = tds[1]?.innerText?.trim();
      const name = tds[2]?.innerText?.trim();
      const cellsTxt = [...tds].map(td => td.innerText.trim());
      const chg = cellsTxt.find(x => /[-+]\d+(?:\.\d+)?%$/.test(x)) || "";
      const mcap = cellsTxt.find(x => /^[\d.]+[MBT]$/.test(x)) || "";
      if (sym) out.push({ sym, name, chg, mcap });
    }
    return out.slice(0, 15);
  });

  let tablaTexto = "";
  if (rows && rows.length) {
    const header = "SYMB   CHANGE   M.CAP   NAME";
    const lines = rows.map(r =>
      `${String(r.sym||"").padEnd(5)}  ${String(r.chg||"").padStart(7)}  ${String(r.mcap||"").padStart(6)}  ${r.name||""}`
    );
    tablaTexto = `<b>Top Tecnología – % y M.Cap</b>\n<pre>${header}\n${lines.join("\n")}</pre>`;
  } else {
    tablaTexto = "(No se pudo extraer la tabla del screener; adjuntamos capturas.)";
  }

  // --- 4) Enviar a Telegram ---
  const fecha = dayjs().format("DD/MM/YYYY");
  await sendPhoto(mapPng, "🗺️ Tecnología — mapa (solo sector, ampliado)");
  if (fs.existsSync(tablePng)) await sendPhoto(tablePng, "📋 Tabla (Screener Finviz)");
  await sendMessage(`📊 <b>Tecnología – Cierre ${fecha}</b>\n\n${tablaTexto}\n\nDesde InvestX (automático).`);

  await browser.close();
})().catch(async (e) => {
  console.error(e);
  try { await sendMessage("⚠️ Error inesperado en Tecnología. Revisa el workflow."); } catch {}
  process.exit(1);
});

