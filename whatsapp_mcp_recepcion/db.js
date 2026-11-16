const { DatabaseSync } = require('node:sqlite');
const path = require('node:path');

const DB_HOSTAL_PATH = path.resolve(__dirname, '..', 'data', 'ovalo_sistema.db');

function ensureColumn(db, table, colDef) {
  try {
    db.exec(`ALTER TABLE ${table} ADD COLUMN ${colDef}`);
  } catch (_) {}
}

function openDb() {
  const db = new DatabaseSync(DB_HOSTAL_PATH);
  db.exec(`
    PRAGMA journal_mode = WAL;
    PRAGMA synchronous = NORMAL;
    PRAGMA busy_timeout = 10000;

    CREATE TABLE IF NOT EXISTS wsp_recepcion_chats (
      jid                  TEXT PRIMARY KEY,
      nombre               TEXT,
      tipo                 TEXT,
      ultimo_mensaje       TEXT,
      fecha_ultimo_mensaje TEXT,
      no_leidos            INTEGER DEFAULT 0,
      es_grupo             INTEGER DEFAULT 0,
      ultimo_mensaje_ts    INTEGER DEFAULT 0,
      actualizado_en       TEXT DEFAULT (datetime('now', 'localtime'))
    );

    CREATE TABLE IF NOT EXISTS wsp_recepcion_mensajes (
      id_mensaje       TEXT PRIMARY KEY,
      id               TEXT,
      jid              TEXT NOT NULL,
      chat_jid         TEXT,
      chat_nombre      TEXT,
      remitente_jid    TEXT,
      nombre_emisor    TEXT,
      remitente_nombre TEXT,
      es_mio           INTEGER DEFAULT 0,
      desde_recepcion  INTEGER DEFAULT 0,
      timestamp_unix   INTEGER,
      timestamp        INTEGER,
      fecha_hora       TEXT,
      tipo_mensaje     TEXT,
      tipo             TEXT,
      texto            TEXT,
      tiene_archivo    INTEGER DEFAULT 0,
      ruta_archivo     TEXT,
      archivo_ruta     TEXT,
      creado_en        TEXT DEFAULT (datetime('now', 'localtime'))
    );

    CREATE TABLE IF NOT EXISTS wsp_recepcion_documentos (
      id                INTEGER PRIMARY KEY AUTOINCREMENT,
      id_mensaje        TEXT UNIQUE,
      mensaje_id        TEXT,
      jid               TEXT,
      chat_nombre       TEXT,
      fecha_hora        TEXT,
      tipo_archivo      TEXT,
      nombre_archivo    TEXT,
      ruta_local        TEXT,
      archivo_ruta      TEXT,
      metodo_extraccion TEXT,
      categoria         TEXT,
      ruc_emisor        TEXT,
      ruc_dni           TEXT,
      serie_numero      TEXT,
      monto_detectado   TEXT,
      operacion_nro     TEXT,
      texto_extraido    TEXT,
      fecha_procesado   TEXT DEFAULT (datetime('now', 'localtime'))
    );

    CREATE TABLE IF NOT EXISTS wsp_recepcion_cola_envio (
      id               INTEGER PRIMARY KEY AUTOINCREMENT,
      telefono_o_jid   TEXT NOT NULL,
      destino_telefono TEXT,
      cliente_nombre   TEXT,
      nro_comprobante  TEXT,
      mensaje          TEXT,
      texto            TEXT,
      ruta_adjunto     TEXT,
      archivo_ruta     TEXT,
      nombre_adjunto   TEXT,
      estado           TEXT DEFAULT 'PENDIENTE',
      detalle_error    TEXT,
      creado_en        TEXT DEFAULT (datetime('now', 'localtime')),
      enviado_en       TEXT
    );

    CREATE TABLE IF NOT EXISTS wsp_recepcion_estado (
      clave          TEXT PRIMARY KEY,
      valor          TEXT,
      actualizado_en TEXT
    );
  `);

  // Asegurar columnas híbridas (compatibilidad total Local + Supabase)
  ensureColumn(db, 'wsp_recepcion_chats', 'es_grupo INTEGER DEFAULT 0');
  ensureColumn(db, 'wsp_recepcion_chats', 'ultimo_mensaje_ts INTEGER DEFAULT 0');
  ensureColumn(db, 'wsp_recepcion_mensajes', 'id TEXT');
  ensureColumn(db, 'wsp_recepcion_mensajes', 'chat_jid TEXT');
  ensureColumn(db, 'wsp_recepcion_mensajes', 'chat_nombre TEXT');
  ensureColumn(db, 'wsp_recepcion_mensajes', 'remitente_nombre TEXT');
  ensureColumn(db, 'wsp_recepcion_mensajes', 'desde_recepcion INTEGER DEFAULT 0');
  ensureColumn(db, 'wsp_recepcion_mensajes', 'timestamp INTEGER');
  ensureColumn(db, 'wsp_recepcion_mensajes', 'tipo TEXT');
  ensureColumn(db, 'wsp_recepcion_mensajes', 'archivo_ruta TEXT');
  ensureColumn(db, 'wsp_recepcion_documentos', 'mensaje_id TEXT');
  ensureColumn(db, 'wsp_recepcion_documentos', 'chat_nombre TEXT');
  ensureColumn(db, 'wsp_recepcion_documentos', 'archivo_ruta TEXT');
  ensureColumn(db, 'wsp_recepcion_documentos', 'ruc_dni TEXT');
  ensureColumn(db, 'wsp_recepcion_cola_envio', 'destino_telefono TEXT');
  ensureColumn(db, 'wsp_recepcion_cola_envio', 'cliente_nombre TEXT');
  ensureColumn(db, 'wsp_recepcion_cola_envio', 'nro_comprobante TEXT');
  ensureColumn(db, 'wsp_recepcion_cola_envio', 'texto TEXT');
  ensureColumn(db, 'wsp_recepcion_cola_envio', 'archivo_ruta TEXT');

  // Asegurar que los triggers de _supabase_outbox usen ON CONFLICT DO UPDATE (compatible con UPSERT de SQLite)
  const triggerSpecs = [
    ['wsp_recepcion_chats', 'jid'],
    ['wsp_recepcion_mensajes', 'id_mensaje'],
    ['wsp_recepcion_documentos', 'id'],
    ['wsp_recepcion_cola_envio', 'id']
  ];
  for (const [tabla, pk] of triggerSpecs) {
    try {
      db.exec(`
        DROP TRIGGER IF EXISTS trg_sb_ins_${tabla};
        DROP TRIGGER IF EXISTS trg_sb_upd_${tabla};
        DROP TRIGGER IF EXISTS trg_sb_del_${tabla};

        CREATE TRIGGER trg_sb_ins_${tabla}
        AFTER INSERT ON ${tabla}
        FOR EACH ROW
        WHEN (SELECT pulling FROM _supabase_sync_control WHERE id = 1) = 0
        BEGIN
          INSERT INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
          VALUES ('${tabla}', CAST(NEW.${pk} AS TEXT), '', 'UPSERT', datetime('now', 'localtime'))
          ON CONFLICT(tabla, pk1, pk2) DO UPDATE SET op = 'UPSERT', updated_at = excluded.updated_at;
        END;

        CREATE TRIGGER trg_sb_upd_${tabla}
        AFTER UPDATE ON ${tabla}
        FOR EACH ROW
        WHEN (SELECT pulling FROM _supabase_sync_control WHERE id = 1) = 0
        BEGIN
          INSERT INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
          VALUES ('${tabla}', CAST(NEW.${pk} AS TEXT), '', 'UPSERT', datetime('now', 'localtime'))
          ON CONFLICT(tabla, pk1, pk2) DO UPDATE SET op = 'UPSERT', updated_at = excluded.updated_at;
        END;

        CREATE TRIGGER trg_sb_del_${tabla}
        AFTER DELETE ON ${tabla}
        FOR EACH ROW
        WHEN (SELECT pulling FROM _supabase_sync_control WHERE id = 1) = 0
        BEGIN
          INSERT INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
          VALUES ('${tabla}', CAST(OLD.${pk} AS TEXT), '', 'DELETE', datetime('now', 'localtime'))
          ON CONFLICT(tabla, pk1, pk2) DO UPDATE SET op = 'DELETE', updated_at = excluded.updated_at;
        END;
      `);
    } catch (_) {}
  }

  return db;
}

function openHostalDb() {
  return openDb();
}

module.exports = {
  openDb,
  openHostalDb,
  DB_PATH: DB_HOSTAL_PATH,
  DB_HOSTAL_PATH
};
