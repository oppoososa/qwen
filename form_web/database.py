# database.py — работа с SQLite через встроенный sqlite3 (без ORM)
import os
import re
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("FORMWEB_DB", os.path.join(BASE_DIR, "forms.db"))

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
PLACEHOLDER_TYPES = ("text", "date", "select", "textarea", "number")

SCHEMA = """
CREATE TABLE IF NOT EXISTS placeholders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT UNIQUE NOT NULL,
    label      TEXT NOT NULL,
    type       TEXT NOT NULL CHECK(type IN
               ('text','date','select','textarea','number')),
    source     TEXT,
    required   INTEGER DEFAULT 0,
    bold       INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS custom_lists (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS custom_list_values (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id  INTEGER NOT NULL,
    value    TEXT NOT NULL,
    FOREIGN KEY(list_id) REFERENCES custom_lists(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS persons (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS addresses (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS templates (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    original_filename TEXT,
    docx_path         TEXT NOT NULL,
    html_path         TEXT NOT NULL,
    is_active         INTEGER DEFAULT 0,
    created_at        TEXT
);

CREATE TABLE IF NOT EXISTS forms (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id  INTEGER,
    data_json    TEXT NOT NULL,
    display_name TEXT,
    created_at   TEXT,
    updated_at   TEXT,
    FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE SET NULL
);
"""


def get_connection():
    """Соединение с БД; FK включены (нужны для ON DELETE CASCADE)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    """Создаёт таблицы и стартовые плейсхолдеры (если их ещё нет)."""
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        cur = conn.execute("SELECT COUNT(*) AS c FROM placeholders")
        if cur.fetchone()["c"] == 0:
            seed = [
                ("person",  "ФИО",   "select", "persons",   0, 1, 1),
                ("date1",   "ДАТА",  "date",   None,        0, 1, 2),
                ("address", "АДРЕС", "select", "addresses", 0, 1, 3),
                ("date2",   "ДАТА2", "date",   None,        0, 1, 4),
            ]
            conn.executemany(
                "INSERT INTO placeholders (key,label,type,source,required,bold,sort_order)"
                " VALUES (?,?,?,?,?,?,?)", seed)
        conn.commit()
    finally:
        conn.close()


# ---------- Простые справочники: persons / addresses ----------

def list_persons(conn=None):
    close = False
    if conn is None:
        conn, close = get_connection(), True
    rows = conn.execute("SELECT * FROM persons ORDER BY full_name").fetchall()
    if close:
        conn.close()
    return rows


def add_person(conn, full_name):
    """Возвращает id; при дубликате бросает sqlite3.IntegrityError."""
    cur = conn.execute("INSERT INTO persons (full_name) VALUES (?)", (full_name,))
    conn.commit()
    return cur.lastrowid


def delete_person(conn, pid):
    conn.execute("DELETE FROM persons WHERE id=?", (pid,))
    conn.commit()


def list_addresses(conn=None):
    close = False
    if conn is None:
        conn, close = get_connection(), True
    rows = conn.execute("SELECT * FROM addresses ORDER BY address").fetchall()
    if close:
        conn.close()
    return rows


def add_address(conn, address):
    cur = conn.execute("INSERT INTO addresses (address) VALUES (?)", (address,))
    conn.commit()
    return cur.lastrowid


def delete_address(conn, aid):
    conn.execute("DELETE FROM addresses WHERE id=?", (aid,))
    conn.commit()


# ---------- Пользовательские списки ----------

def list_custom_lists(conn=None):
    close = False
    if conn is None:
        conn, close = get_connection(), True
    rows = conn.execute(
        """SELECT cl.*, COUNT(clv.id) AS cnt
           FROM custom_lists cl
           LEFT JOIN custom_list_values clv ON clv.list_id = cl.id
           GROUP BY cl.id ORDER BY cl.name""").fetchall()
    if close:
        conn.close()
    return rows


def get_custom_list(conn, lid):
    return conn.execute("SELECT * FROM custom_lists WHERE id=?", (lid,)).fetchone()


def add_custom_list(conn, name):
    """name должен соответствовать ^[a-z][a-z0-9_]+$ (валидация выше уровня SQL)."""
    cur = conn.execute("INSERT INTO custom_lists (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid


def delete_custom_list(conn, lid):
    # значения удалятся каскадом (ON DELETE CASCADE, PRAGMA foreign_keys=ON)
    conn.execute("DELETE FROM custom_lists WHERE id=?", (lid,))
    conn.commit()


def list_custom_values(conn, lid):
    return conn.execute(
        "SELECT * FROM custom_list_values WHERE list_id=? ORDER BY value", (lid,)
    ).fetchall()


def add_custom_value(conn, lid, value):
    cur = conn.execute(
        "INSERT INTO custom_list_values (list_id, value) VALUES (?,?)", (lid, value))
    conn.commit()
    return cur.lastrowid


def delete_custom_value(conn, vid):
    conn.execute("DELETE FROM custom_list_values WHERE id=?", (vid,))
    conn.commit()


# ---------- Плейсхолдеры ----------

def list_placeholders(conn=None):
    close = False
    if conn is None:
        conn, close = get_connection(), True
    rows = conn.execute(
        "SELECT * FROM placeholders ORDER BY sort_order, id").fetchall()
    if close:
        conn.close()
    return rows


def get_placeholder(conn, pid):
    return conn.execute("SELECT * FROM placeholders WHERE id=?", (pid,)).fetchone()


def get_placeholder_by_key(conn, key):
    return conn.execute("SELECT * FROM placeholders WHERE key=?", (key,)).fetchone()


def add_placeholder(conn, key, label, ptype, source=None,
                    required=0, bold=0, sort_order=0):
    """Возвращает id. IntegrityError — при дубликате key."""
    cur = conn.execute(
        """INSERT INTO placeholders (key,label,type,source,required,bold,sort_order)
           VALUES (?,?,?,?,?,?,?)""",
        (key, label, ptype, source or None, int(required), int(bold), int(sort_order)))
    conn.commit()
    return cur.lastrowid


def update_placeholder(conn, pid, key, label, ptype, source=None,
                       required=0, bold=0, sort_order=0):
    conn.execute(
        """UPDATE placeholders SET key=?,label=?,type=?,source=?,
           required=?,bold=?,sort_order=? WHERE id=?""",
        (key, label, ptype, source or None,
         int(required), int(bold), int(sort_order), pid))
    conn.commit()


def delete_placeholder(conn, pid):
    conn.execute("DELETE FROM placeholders WHERE id=?", (pid,))
    conn.commit()


# ---------- Шаблоны ----------

def list_templates(conn=None):
    close = False
    if conn is None:
        conn, close = get_connection(), True
    rows = conn.execute("SELECT * FROM templates ORDER BY created_at DESC, id DESC").fetchall()
    if close:
        conn.close()
    return rows


def get_template(conn, tid):
    return conn.execute("SELECT * FROM templates WHERE id=?", (tid,)).fetchone()


def get_active_template(conn=None):
    close = False
    if conn is None:
        conn, close = get_connection(), True
    row = conn.execute("SELECT * FROM templates WHERE is_active=1 ORDER BY id LIMIT 1").fetchone()
    if close:
        conn.close()
    return row


def add_template(conn, name, original_filename, docx_path, html_path, is_active=0):
    created = now_iso()
    cur = conn.execute(
        """INSERT INTO templates (name, original_filename, docx_path, html_path,
                                  is_active, created_at)
           VALUES (?,?,?,?,?,?)""",
        (name, original_filename, docx_path, html_path, int(is_active), created))
    conn.commit()
    return cur.lastrowid


def activate_template(conn, tid):
    conn.execute("UPDATE templates SET is_active=0")
    conn.execute("UPDATE templates SET is_active=1 WHERE id=?", (tid,))
    conn.commit()


def delete_template(conn, tid):
    conn.execute("DELETE FROM templates WHERE id=?", (tid,))
    conn.commit()


# ---------- Сохранённые формы ----------

def list_forms(conn=None):
    close = False
    if conn is None:
        conn, close = get_connection(), True
    rows = conn.execute(
        """SELECT f.*, t.name AS template_name
           FROM forms f LEFT JOIN templates t ON t.id = f.template_id
           ORDER BY f.updated_at DESC, f.id DESC""").fetchall()
    if close:
        conn.close()
    return rows


def get_form(conn, fid):
    return conn.execute("SELECT * FROM forms WHERE id=?", (fid,)).fetchone()


def save_form(conn, data_json, template_id=None, display_name=None, form_id=None):
    """Создаёт или обновляет запись формы. Возвращает id записи."""
    ts = now_iso()
    if form_id:
        conn.execute(
            """UPDATE forms SET data_json=?, display_name=?, template_id=?, updated_at=?
               WHERE id=?""", (data_json, display_name, template_id, ts, form_id))
        conn.commit()
        return form_id
    cur = conn.execute(
        """INSERT INTO forms (template_id, data_json, display_name, created_at, updated_at)
           VALUES (?,?,?,?,?)""",
        (template_id, data_json, display_name, ts, ts))
    conn.commit()
    return cur.lastrowid


def delete_form(conn, fid):
    conn.execute("DELETE FROM forms WHERE id=?", (fid,))
    conn.commit()
