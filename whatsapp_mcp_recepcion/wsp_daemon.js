const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');
const pino = require('pino');
const QRCode = require('qrcode');
const { openDb } = require('./db');

const OCR_SCRIPT = path.join(__dirname, 'ocr_extractor.py');
let ocrRunning = false;
let ocrPendingAgain = false;

function triggerSupabasePush() {
  try {
    const req = http.request('http://127.0.0.1:8080/api/supabase/status', { method: 'GET', timeout: 3500 }, (res) => {
      res.resume();
    });
    req.on('error', () => {});
    req.end();
  } catch (_) {}
}

function triggerOcrPendientes() {
  if (ocrRunning) {
    ocrPendingAgain = true;
    return;
  }
  ocrRunning = true;
  const child = spawn('python', [OCR_SCRIPT, '--pendientes'], { windowsHide: true });
  child.on('close', () => {
    ocrRunning = false;
    triggerSupabasePush();
    if (ocrPendingAgain) {
      ocrPendingAgain = false;
      triggerOcrPendientes();
    }
  });
}

const AUTH_DIR = path.join(__dirname, 'auth_info');
const MEDIA_DIR = path.join(__dirname, 'media');
const QR_PATH = path.join(__dirname, 'qr_actual.png');
const QR_COMPAT_PATH = path.join(__dirname, 'qr_whatsapp.png');
const ARTIFACT_QR_PATH = 'C:\\Users\\ovalo mariategui\\.gemini\\antigravity\\brain\\5527a9da-cc0a-433a-81dc-ace1aa7e7319\\qr_actual.png';

fs.mkdirSync(AUTH_DIR, { recursive: true });
fs.mkdirSync(MEDIA_DIR, { recursive: true });

const db = openDb();

const upsertEstado = db.prepare(`
  INSERT INTO wsp_recepcion_estado (clave, valor, actualizado_en)
  VALUES (?, ?, ?)
  ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, actualizado_en = excluded.actualizado_en
`);

const upsertEstadoSistema = db.prepare(`
  INSERT INTO estado_sistema (clave, valor, updated_at)
  VALUES (?, ?, ?)
  ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, updated_at = excluded.updated_at
`);

const upsertChat = db.prepare(`
  INSERT INTO wsp_recepcion_chats (
    jid, nombre, tipo, ultimo_mensaje, fecha_ultimo_mensaje, no_leidos, es_grupo, ultimo_mensaje_ts, actualizado_en
  ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  ON CONFLICT(jid) DO UPDATE SET
    nombre = COALESCE(excluded.nombre, wsp_recepcion_chats.nombre),
    tipo = COALESCE(excluded.tipo, wsp_recepcion_chats.tipo),
    ultimo_mensaje = COALESCE(excluded.ultimo_mensaje, wsp_recepcion_chats.ultimo_mensaje),
    fecha_ultimo_mensaje = COALESCE(excluded.fecha_ultimo_mensaje, wsp_recepcion_chats.fecha_ultimo_mensaje),
    es_grupo = excluded.es_grupo,
    ultimo_mensaje_ts = MAX(COALESCE(wsp_recepcion_chats.ultimo_mensaje_ts, 0), excluded.ultimo_mensaje_ts),
    actualizado_en = excluded.actualizado_en
`);

const getChatName = db.prepare(`SELECT nombre FROM wsp_recepcion_chats WHERE jid = ?`);

const upsertMsg = db.prepare(`
  INSERT INTO wsp_recepcion_mensajes (
    id_mensaje, id, jid, chat_jid, chat_nombre,
    remitente_jid, nombre_emisor, remitente_nombre,
    es_mio, desde_recepcion, timestamp_unix, timestamp,
    fecha_hora, tipo_mensaje, tipo, texto,
    tiene_archivo, ruta_archivo, archivo_ruta, creado_en
  ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  ON CONFLICT(id_mensaje) DO UPDATE SET
    texto = COALESCE(excluded.texto, wsp_recepcion_mensajes.texto),
    tiene_archivo = MAX(wsp_recepcion_mensajes.tiene_archivo, excluded.tiene_archivo),
    ruta_archivo = COALESCE(excluded.ruta_archivo, wsp_recepcion_mensajes.ruta_archivo),
    archivo_ruta = COALESCE(excluded.archivo_ruta, wsp_recepcion_mensajes.archivo_ruta)
`);

// Convierte de forma segura cualquier Long de Protobuf o número a entero Unix en segundos
function toUnixSeconds(raw) {
  if (!raw) return 0;
  if (typeof raw === 'number') return Number.isFinite(raw) ? Math.floor(raw) : 0;
  if (typeof raw === 'bigint') return Number(raw);
  if (typeof raw === 'object') {
    if (typeof raw.low === 'number') return raw.low;
    if (typeof raw.toNumber === 'function') {
      const n = raw.toNumber();
      return Number.isFinite(n) ? Math.floor(n) : 0;
    }
  }
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? Math.floor(parsed) : 0;
}

function formatLocalIso(tsSeconds) {
  const safeTs = toUnixSeconds(tsSeconds) || Math.floor(Date.now() / 1000);
  const d = new Date(safeTs * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function setStatus(key, val) {
  const nowStr = formatLocalIso();
  try {
    upsertEstado.run(key, String(val), nowStr);
    upsertEstadoSistema.run(`wsp_recepcion_${key}`, String(val), nowStr);
  } catch (_) {}
}

function extractMessageContent(msg) {
  const m = msg.message;
  if (!m) return { tipo: 'sistema', texto: '', mediaMsg: null, mimetype: null };

  const inner = m.ephemeralMessage?.message
    || m.viewOnceMessage?.message
    || m.viewOnceMessageV2?.message
    || m.documentWithCaptionMessage?.message
    || m;

  if (inner.conversation) {
    return { tipo: 'texto', texto: inner.conversation, mediaMsg: null, mimetype: null };
  }
  if (inner.extendedTextMessage?.text) {
    return { tipo: 'texto', texto: inner.extendedTextMessage.text, mediaMsg: null, mimetype: null };
  }
  if (inner.imageMessage) {
    return {
      tipo: 'imagen',
      texto: inner.imageMessage.caption || '[Imagen]',
      mediaMsg: inner.imageMessage,
      mimetype: inner.imageMessage.mimetype || 'image/jpeg',
      ext: 'jpg'
    };
  }
  if (inner.documentMessage) {
    const fileName = inner.documentMessage.fileName || 'documento';
    const caption = inner.documentMessage.caption ? ` - ${inner.documentMessage.caption}` : '';
    return {
      tipo: 'documento',
      texto: `[Documento: ${fileName}]${caption}`,
      mediaMsg: inner.documentMessage,
      mimetype: inner.documentMessage.mimetype || 'application/octet-stream',
      fileName
    };
  }
  if (inner.videoMessage) {
    return {
      tipo: 'video',
      texto: inner.videoMessage.caption || '[Video]',
      mediaMsg: null,
      mimetype: inner.videoMessage.mimetype || 'video/mp4'
    };
  }
  if (inner.audioMessage) {
    return { tipo: 'audio', texto: '[Audio / Nota de voz]', mediaMsg: null, mimetype: inner.audioMessage.mimetype };
  }
  if (inner.stickerMessage) {
    return { tipo: 'sticker', texto: '[Sticker]', mediaMsg: null, mimetype: 'image/webp' };
  }
  return { tipo: 'otro', texto: '', mediaMsg: null, mimetype: null };
}

function isIgnoredJid(jid) {
  if (!jid || jid === 'status@broadcast') return true;
  if (jid.endsWith('@newsletter')) return true;
  return false;
}

const lidToPnMap = new Map();

function resolveJid(jid, altJid) {
  if (altJid && altJid.endsWith('@s.whatsapp.net')) return altJid;
  if (!jid) return '';
  if (lidToPnMap.has(jid)) return lidToPnMap.get(jid);
  return jid;
}

function saveMessageRecord(msg, archivoRuta = null) {
  try {
    if (!msg?.key || isIgnoredJid(msg.key.remoteJid)) return;
    if (msg.key.remoteJid?.endsWith('@lid') && msg.key.remoteJidAlt?.endsWith('@s.whatsapp.net')) {
      lidToPnMap.set(msg.key.remoteJid, msg.key.remoteJidAlt);
    }
    const chatJid = resolveJid(msg.key.remoteJid, msg.key.remoteJidAlt);
    if (!chatJid || isIgnoredJid(chatJid)) return;

    const msgId = String(msg.key.id || `${Date.now()}`);
    const fromMe = msg.key.fromMe ? 1 : 0;
    const ts = toUnixSeconds(msg.messageTimestamp) || Math.floor(Date.now() / 1000);
    const { tipo, texto } = extractMessageContent(msg);
    if (!texto && !archivoRuta && (tipo === 'otro' || tipo === 'sistema')) return;

    const pushName = msg.pushName || null;
    const senderJid = resolveJid(msg.key.participant || (fromMe ? 'recepcion' : chatJid), msg.key.participantAlt);
    const isGroup = chatJid.endsWith('@g.us') ? 1 : 0;
    const tipoChat = isGroup ? 'grupo' : 'individual';
    const fechaHora = formatLocalIso(ts);
    const nowStr = formatLocalIso();

    try {
      if (!isGroup && !fromMe && pushName) {
        upsertChat.run(chatJid, pushName, tipoChat, texto, fechaHora, 0, 0, ts, nowStr);
      } else {
        upsertChat.run(chatJid, null, tipoChat, texto, fechaHora, 0, isGroup, ts, nowStr);
      }
    } catch (_) {}

    const rowChat = getChatName.get(chatJid);
    const chatNombre = rowChat?.nombre || pushName || chatJid;
    const emisorNombre = pushName || rowChat?.nombre || senderJid;

    upsertMsg.run(
      msgId,
      msgId,
      chatJid,
      chatJid,
      chatNombre,
      senderJid,
      emisorNombre,
      emisorNombre,
      fromMe,
      fromMe,
      ts,
      ts,
      fechaHora,
      tipo,
      tipo,
      texto,
      archivoRuta ? 1 : 0,
      archivoRuta,
      archivoRuta,
      nowStr
    );
  } catch (err) {
    console.error('Error guardando mensaje:', err.message);
  }
}

async function startWhatsApp() {
  const baileys = await import('@whiskeysockets/baileys');
  const makeWASocket = baileys.default?.default || baileys.default || baileys.makeWASocket;
  const { useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion, downloadMediaMessage, Browsers } = baileys;

  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  let version = [2, 3000, 1015901307];
  try {
    const vInfo = await fetchLatestBaileysVersion();
    if (vInfo?.version) version = vInfo.version;
  } catch (_) {}

  let isConnected = false;
  let queueTimer = null;

  const sock = makeWASocket({
    version,
    auth: state,
    logger: pino({ level: 'silent' }),
    printQRInTerminal: false,
    syncFullHistory: true,
    shouldSyncHistoryMessage: () => true,
    browser: ['Ovalo Recepcion Laptop2', 'Edge', '120.0.0']
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      await QRCode.toFile(QR_PATH, qr, { width: 420, margin: 2 });
      try { fs.copyFileSync(QR_PATH, QR_COMPAT_PATH); } catch (_) {}
      try { fs.copyFileSync(QR_PATH, ARTIFACT_QR_PATH); } catch (_) {}
      setStatus('conexion', 'ESPERANDO_QR');
      setStatus('qr_path', QR_PATH);
      triggerSupabasePush();
      console.log('QR_GENERADO:', QR_PATH);
    }

    if (connection === 'open') {
      isConnected = true;
      const meJid = sock.user?.id || 'conectado';
      setStatus('conexion', 'CONECTADO');
      setStatus('usuario_actual', meJid);
      triggerSupabasePush();
      console.log('WHATSAPP_RECEPCION_CONECTADO:', meJid);
      try {
        const msgCount = db.prepare(`SELECT COUNT(*) AS n FROM wsp_recepcion_mensajes`).get().n;
        if (msgCount === 0) {
          const selfJid = meJid.split(':')[0] + '@s.whatsapp.net';
          const sent = await sock.sendMessage(selfJid, { text: '✅ WhatsApp Recepción Óvalo activo en vivo y sincronizado con ovalo_sistema.db y Supabase.' });
          if (sent) saveMessageRecord(sent, null);
          triggerSupabasePush();
        }
      } catch (_) {}
    }

    if (connection === 'close') {
      isConnected = false;
      if (queueTimer) {
        clearInterval(queueTimer);
        queueTimer = null;
      }
      const code = lastDisconnect?.error?.output?.statusCode;
      setStatus('conexion', `DESCONECTADO_${code || 'UNKNOWN'}`);
      triggerSupabasePush();
      if (code !== DisconnectReason.loggedOut) {
        setTimeout(() => startWhatsApp(), 4000);
      } else {
        console.log('WHATSAPP_RECEPCION_LOGGED_OUT');
      }
    }
  });

  sock.ev.on('messaging-history.set', ({ chats = [], contacts = [], messages = [], lidPnMappings = [] }) => {
    try {
      for (const m of lidPnMappings) {
        if (m.lid && m.pn) lidToPnMap.set(m.lid, m.pn);
      }
      const nowStr = formatLocalIso();
      db.exec('BEGIN');
      for (const c of chats) {
        if (!c?.id || isIgnoredJid(c.id)) continue;
        if (c.id.endsWith('@lid') && c.pnJid) lidToPnMap.set(c.id, c.pnJid);
        const jid = resolveJid(c.id, c.pnJid);
        const isGroup = jid.endsWith('@g.us') ? 1 : 0;
        const tipoChat = isGroup ? 'grupo' : 'individual';
        const ts = toUnixSeconds(c.conversationTimestamp || c.lastMessageRecvTimestamp);
        upsertChat.run(jid, c.name || c.displayName || c.subject || null, tipoChat, null, ts ? formatLocalIso(ts) : null, Number(c.unreadCount || 0), isGroup, ts, nowStr);
      }
      for (const ct of contacts) {
        if (!ct?.id || isIgnoredJid(ct.id)) continue;
        if (ct.id.endsWith('@lid') && ct.phoneNumber) lidToPnMap.set(ct.id, ct.phoneNumber);
        const jid = resolveJid(ct.id, ct.phoneNumber);
        const cname = ct.name || ct.notify || ct.verifiedName || null;
        if (cname) {
          const isGroup = jid.endsWith('@g.us') ? 1 : 0;
          const tipoChat = isGroup ? 'grupo' : 'individual';
          upsertChat.run(jid, cname, tipoChat, null, null, 0, isGroup, 0, nowStr);
        }
      }
      for (const m of messages) {
        saveMessageRecord(m, null);
      }
      db.exec('COMMIT');
      setStatus('ultimo_sync_historial', `${messages.length} msgs / ${chats.length} chats`);
      triggerSupabasePush();
      console.log(`HISTORIAL_RECEPCION_SINCRONIZADO: ${messages.length} mensajes, ${chats.length} chats, ${contacts.length} contactos`);
    } catch (err) {
      console.error('Error en messaging-history.set:', err.message);
      try { db.exec('ROLLBACK'); } catch (__) {}
    }
  });

  sock.ev.on('chats.upsert', (chats) => {
    const nowStr = formatLocalIso();
    for (const c of chats) {
      if (!c?.id || isIgnoredJid(c.id)) continue;
      const jid = resolveJid(c.id, c.pnJid);
      const isGroup = jid.endsWith('@g.us') ? 1 : 0;
      const tipoChat = isGroup ? 'grupo' : 'individual';
      const ts = toUnixSeconds(c.conversationTimestamp || c.lastMessageRecvTimestamp);
      upsertChat.run(jid, c.name || c.subject || null, tipoChat, null, ts ? formatLocalIso(ts) : null, Number(c.unreadCount || 0), isGroup, ts, nowStr);
    }
    triggerSupabasePush();
  });

  sock.ev.on('contacts.upsert', (contacts) => {
    const nowStr = formatLocalIso();
    for (const ct of contacts) {
      if (!ct?.id || isIgnoredJid(ct.id)) continue;
      const jid = resolveJid(ct.id, ct.phoneNumber);
      const cname = ct.name || ct.notify || ct.verifiedName || null;
      if (cname) {
        const isGroup = jid.endsWith('@g.us') ? 1 : 0;
        const tipoChat = isGroup ? 'grupo' : 'individual';
        upsertChat.run(jid, cname, tipoChat, null, null, 0, isGroup, 0, nowStr);
      }
    }
    triggerSupabasePush();
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    let changed = false;
    for (const m of messages) {
      if (!m?.key || isIgnoredJid(m.key.remoteJid)) continue;
      let savedFile = null;
      const info = extractMessageContent(m);
      if (type === 'notify' && info.mediaMsg && (info.tipo === 'documento' || info.tipo === 'imagen')) {
        try {
          const buffer = await downloadMediaMessage(m, 'buffer', {}, { logger: pino({ level: 'silent' }), reuploadRequest: sock.updateMediaMessage });
          const safeName = (info.fileName || `${m.key.id}.${info.ext || 'bin'}`).replace(/[^a-zA-Z0-9._-]/g, '_');
          savedFile = path.join(MEDIA_DIR, `${Date.now()}_${safeName}`);
          fs.writeFileSync(savedFile, buffer);
        } catch (_) {}
      }
      saveMessageRecord(m, savedFile);
      changed = true;
      if (savedFile) {
        triggerOcrPendientes();
      }
    }
    if (changed) {
      triggerSupabasePush();
    }
  });

  const getPending = db.prepare(`SELECT * FROM wsp_recepcion_cola_envio WHERE estado = 'PENDIENTE' ORDER BY id ASC LIMIT 5`);
  const claimPending = db.prepare(`UPDATE wsp_recepcion_cola_envio SET estado = 'ENVIANDO' WHERE id = ? AND estado = 'PENDIENTE'`);
  const markSent = db.prepare(`UPDATE wsp_recepcion_cola_envio SET estado = 'ENVIADO', enviado_en = ? WHERE id = ?`);
  const markAlreadySent = db.prepare(`UPDATE wsp_recepcion_cola_envio SET estado = 'YA_ENVIADO_PREVIO', enviado_en = ?, detalle_error = ? WHERE id = ?`);
  const markErr = db.prepare(`UPDATE wsp_recepcion_cola_envio SET estado = 'ERROR', detalle_error = ? WHERE id = ?`);
  const checkRecentSentCpe = db.prepare(`
    SELECT 1 FROM wsp_recepcion_mensajes
     WHERE COALESCE(chat_jid, jid) = ?
       AND (COALESCE(es_mio, 0) = 1 OR COALESCE(desde_recepcion, 0) = 1)
       AND COALESCE(timestamp_unix, timestamp, 0) >= ?
       AND UPPER(COALESCE(texto, '')) LIKE ?
     LIMIT 1
  `);

  let queueBusy = false;
  queueTimer = setInterval(async () => {
    if (queueBusy || !isConnected) return;
    queueBusy = true;
    try {
      const rows = getPending.all();
      for (const r of rows) {
        try {
          const claimRes = claimPending.run(r.id);
          if (!claimRes || Number(claimRes.changes || 0) === 0) {
            continue;
          }
          let jid = (r.telefono_o_jid || r.destino_telefono || '').trim();
          if (!jid.includes('@')) {
            jid = jid.replace(/\D/g, '');
            if (jid.length === 9) jid = `51${jid}`;
            jid = `${jid}@s.whatsapp.net`;
          }
          const rutaAdj = r.ruta_adjunto || r.archivo_ruta;
          const textoMsg = r.mensaje || r.texto || '';
          const cpeMatch = String(r.nro_comprobante || textoMsg || rutaAdj || '').match(/((?:E001|EB01|F\d{3}|B\d{3})-\d+)/i);
          if (cpeMatch) {
            const cpeCode = cpeMatch[1].toUpperCase();
            const minTs = Math.floor(Date.now() / 1000) - 1800;
            const dup = checkRecentSentCpe.get(jid, minTs, `%${cpeCode}%`);
            if (dup) {
              markAlreadySent.run(formatLocalIso(), `Ya enviado previamente (${cpeCode}) en los últimos 30 min`, r.id);
              triggerSupabasePush();
              continue;
            }
          }
          let sentMsg = null;
          if (rutaAdj && fs.existsSync(rutaAdj)) {
            const ext = path.extname(rutaAdj).toLowerCase();
            const fileName = r.nombre_adjunto || path.basename(rutaAdj);
            const buf = fs.readFileSync(rutaAdj);
            if (['.jpg', '.jpeg', '.png'].includes(ext)) {
              sentMsg = await sock.sendMessage(jid, { image: buf, caption: textoMsg });
            } else {
              const mime = ext === '.pdf' ? 'application/pdf' : 'application/octet-stream';
              sentMsg = await sock.sendMessage(jid, { document: buf, mimetype: mime, fileName, caption: textoMsg });
            }
          } else if (textoMsg) {
            sentMsg = await sock.sendMessage(jid, { text: textoMsg });
          }
          if (sentMsg) {
            saveMessageRecord(sentMsg, rutaAdj || null);
          }
          markSent.run(formatLocalIso(), r.id);
          triggerSupabasePush();
        } catch (err) {
          markErr.run(String(err.message || err), r.id);
          triggerSupabasePush();
        }
      }
    } catch (_) {
    } finally {
      queueBusy = false;
    }
  }, 2500);
}

try {
  require('./visor_web');
} catch (_) {}

startWhatsApp().catch((err) => {
  console.error('Error fatal en wsp_daemon Recepción:', err);
});
