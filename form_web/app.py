# app.py — «Конструктор форм»: Flask-приложение для локального запуска
import html as html_mod
import json
import os
import re
import subprocess
import uuid

import mammoth
from flask import (Flask, flash, redirect, render_template, request,
                   send_file, url_for)

import database as db
import printer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

app = Flask(__name__)
app.secret_key = os.environ.get("FORMWEB_SECRET", "form-constructor-local-secret")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 МБ на шаблон

PLACEHOLDER_RE = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")


def ensure_upload_dir():
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def get_conn():
    return db.get_connection()


# ---------- Рендер активного шаблона ----------

def load_source_options(conn, source):
    """Список строк-вариантов для select по значению source."""
    if not source:
        return []
    if source == "persons":
        return [r["full_name"] for r in db.list_persons(conn)]
    if source == "addresses":
        return [r["address"] for r in db.list_addresses(conn)]
    m = re.match(r"^list:(\d+)$", source)
    if m:
        return [r["value"] for r in db.list_custom_values(conn, int(m.group(1)))]
    return []


def render_widget(conn, ph, key):
    """HTML элемента ввода для плейсхолдера (или unregistered input)."""
    k = html_mod.escape(key)
    if ph is None:
        title = ("Поле не настроено. Добавьте его на странице 'Поля формы'").replace("'", "&#39;")
        return (f'<input type="text" name="{k}" class="form-input unregistered" '
                f'title="{title}">')
    t = ph["type"]
    if t == "text":
        return f'<input type="text" name="{k}" class="form-input">'
    if t == "date":
        return f'<input type="date" name="{k}" class="form-input">'
    if t == "number":
        return f'<input type="number" name="{k}" class="form-input">'
    if t == "textarea":
        return f'<textarea name="{k}" class="form-input"></textarea>'
    if t == "select":
        opts = ['<option value="">— выберите —</option>']
        for val in load_source_options(conn, ph["source"]):
            v = html_mod.escape(str(val))
            opts.append(f'<option value="{v}">{v}</option>')
        return (f'<select name="{k}" class="form-input">' + "".join(opts) + "</select>")
    return f'<input type="text" name="{k}" class="form-input">'


def apply_values(html_body, values):
    """Подстановка сохранённых значений в уже отрендеренные виджеты."""
    for key, val in values.items():
        val = str(val)
        esc_attr = html_mod.escape(val, quote=True)
        # input/textarea/select: добавляем value / содержимое
        pattern = re.compile(
            r'(<(?:input|textarea|select)\b[^>]*?name="' + re.escape(key) + r'"[^>]*?>)')

        def repl(m):
            tag = m.group(1)
            if tag.startswith("<input"):
                if 'value=' in tag:
                    tag = re.sub(r'value="[^"]*"', f'value="{esc_attr}"', tag)
                else:
                    tag = tag[:-1] + f' value="{esc_attr}">'
                return tag
            if tag.startswith("<textarea"):
                return tag + html_mod.escape(val) + "</textarea>"
            # <select ...> — закроем и перерисуем options с selected
            return tag  # обработка selected ниже отдельным проходом
        html_body = pattern.sub(repl, html_body)

        # для select — помечаем нужный option как selected
        sel_pat = re.compile(
            r'(<select\b[^>]*?name="' + re.escape(key) + r'"[^>]*?>)(.*?)(</select>)',
            re.S)
        sm = sel_pat.search(html_body)
        if sm:
            inner = sm.group(2)
            opt_pat = re.compile(r'<option value="([^"]*)">')

            def mark_opt(om):
                if om.group(1) == val:
                    return om.group(0)[:-1] + ' selected>'
                return om.group(0)
            inner = opt_pat.sub(mark_opt, inner)
            html_body = html_body[:sm.start()] + sm.group(1) + inner + sm.group(3) + html_body[sm.end():]
    return html_body


def render_template_html(conn, tpl_row, values=None):
    """Читает uploads/<uuid>.html, заменяет {{key}} на виджеты формы."""
    with open(tpl_row["html_path"], "r", encoding="utf-8") as f:
        body = f.read()
    ph_map = {p["key"]: p for p in db.list_placeholders(conn)}

    def sub(m):
        key = m.group(1)
        ph = ph_map.get(key)
        widget = render_widget(conn, ph, key)
        if ph is not None and ph["bold"]:
            label = html_mod.escape(ph["label"])
            return f"<strong>{label}</strong>: {widget}"
        return widget

    body = PLACEHOLDER_RE.sub(sub, body)
    if values:
        body = apply_values(body, values)
    return body


def collect_keys_from_template(conn, tpl_row):
    """Все ключи {{...}}, встречающиеся в HTML активного шаблона."""
    with open(tpl_row["html_path"], "r", encoding="utf-8") as f:
        body = f.read()
    seen, keys = set(), []
    for m in PLACEHOLDER_RE.finditer(body):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            keys.append(m.group(1))
    return keys


# ---------- Маршруты ----------

@app.route("/")
def index():
    conn = get_conn()
    try:
        tpl = db.get_active_template(conn)
        if tpl is None:
            return render_template("form.html", template=None, body_html=None)
        body = render_template_html(conn, tpl)
        return render_template("form.html", template=tpl, body_html=body,
                               form_id=None, form_data={})
    finally:
        conn.close()


@app.route("/form/<int:fid>")
def open_form(fid):
    conn = get_conn()
    try:
        form = db.get_form(conn, fid)
        if form is None:
            flash("Форма не найдена.", "error")
            return redirect(url_for("load"))
        tpl = db.get_active_template(conn)
        if tpl is None:
            flash("Нет активного шаблона — сначала загрузите шаблон.", "error")
            return redirect(url_for("templates_page"))
        values = json.loads(form["data_json"])
        body = render_template_html(conn, tpl, values)
        return render_template("form.html", template=tpl, body_html=body,
                               form_id=fid, form_data=values)
    finally:
        conn.close()


def build_display_name(values):
    person = values.get("person")
    date = values.get("date1") or ""
    if person:
        return f"{person} — {date}" if date else person
    first = next((v for k, v in values.items()
                  if v and k not in ("date1", "date2")), "")
    if first:
        return f"{first} — {date}" if date else first
    return date or "Без имени"


@app.route("/save", methods=["POST"])
def save():
    conn = get_conn()
    try:
        tpl = db.get_active_template(conn)
        if tpl is None:
            flash("Нет активного шаблона — сохранение невозможно.", "error")
            return redirect(url_for("index"))
        keys = collect_keys_from_template(conn, tpl)
        values = {k: request.form.get(k, "").strip() for k in keys}
        display_name = build_display_name(values)
        data_json = json.dumps(values, ensure_ascii=False)
        form_id_raw = request.form.get("form_id", "")
        form_id = int(form_id_raw) if form_id_raw.isdigit() else None
        if form_id and db.get_form(conn, form_id) is None:
            form_id = None
        saved_id = db.save_form(conn, data_json, template_id=tpl["id"],
                                display_name=display_name, form_id=form_id)
        flash(f"Форма «{display_name}» сохранена.", "success")
        return redirect(url_for("open_form", fid=saved_id))
    finally:
        conn.close()


@app.route("/load")
def load():
    conn = get_conn()
    try:
        forms = db.list_forms(conn)
        return render_template("load.html", forms=forms)
    finally:
        conn.close()


@app.route("/form/<int:fid>/delete", methods=["POST"])
def delete_form(fid):
    conn = get_conn()
    try:
        db.delete_form(conn, fid)
        flash("Форма удалена.", "success")
        return redirect(url_for("load"))
    finally:
        conn.close()


# ---------- Скачивание DOCX ----------

@app.route("/download_docx", methods=["POST"])
def download_docx():
    conn = get_conn()
    try:
        tpl = db.get_active_template(conn)
        if tpl is None:
            flash("Нет активного шаблона.", "error")
            return redirect(url_for("index"))
        keys = collect_keys_from_template(conn, tpl)
        values = {k: request.form.get(k, "").strip() for k in keys}
        display_name = build_display_name(values)
        try:
            out_path = printer.fill_docx(tpl["docx_path"], values)
        except Exception as e:
            flash(f"Не удалось заполнить DOCX ({e}). Используйте кнопку «Печать».", "error")
            return redirect(request.referrer or url_for("index"))
        download_name = printer.build_download_name(display_name)
        resp = send_file(out_path, as_attachment=True, download_name=download_name)

        @resp.call_on_close
        def _cleanup():
            os.remove(out_path)
        return resp
    finally:
        conn.close()


# ---------- Шаблоны ----------

def convert_odt_to_docx(odt_path):
    """ODT -> DOCX через LibreOffice headless. Возвращает путь к DOCX."""
    try:
        subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "docx",
             "--outdir", UPLOAD_DIR, odt_path],
            check=True, capture_output=True, timeout=120)
    except FileNotFoundError:
        raise RuntimeError("LibreOffice не найден. Установите: sudo apt install libreoffice-writer")
    except subprocess.CalledProcessError as e:
        raise RuntimeError("Ошибка конвертации ODT: " +
                           (e.stderr.decode("utf-8", "replace") if e.stderr else "см. лог"))
    result = os.path.splitext(odt_path)[0] + ".docx"
    if not os.path.exists(result):
        raise RuntimeError("LibreOffice не создал DOCX-файл")
    return result


@app.route("/templates")
def templates_page():
    conn = get_conn()
    try:
        tpls = db.list_templates(conn)
        return render_template("template_list.html", templates=tpls)
    finally:
        conn.close()


@app.route("/templates/upload", methods=["POST"])
def upload_template():
    file = request.files.get("file")
    name = (request.form.get("name") or "").strip()
    if file is None or file.filename == "":
        flash("Выберите файл .docx или .odt.", "error")
        return redirect(url_for("templates_page"))
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".docx", ".odt"):
        flash("Можно загружать только файлы .docx или .odt.", "error")
        return redirect(url_for("templates_page"))

    ensure_upload_dir()
    uid = uuid.uuid4().hex  # имя файла — uuid, защита от path traversal
    orig_filename = file.filename
    stored_ext_path = os.path.join(UPLOAD_DIR, uid + ext)
    file.save(stored_ext_path)

    conn = get_conn()
    try:
        if ext == ".odt":
            try:
                docx_path = convert_odt_to_docx(stored_ext_path)
            except RuntimeError as e:
                os.remove(stored_ext_path)
                flash(str(e), "error")
                return redirect(url_for("templates_page"))
        else:
            docx_path = stored_ext_path

        with open(docx_path, "rb") as f:
            html_body = mammoth.convert_to_html(f).value
        html_path = os.path.join(UPLOAD_DIR, uid + ".html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_body)

        has_active = db.get_active_template(conn) is not None
        tpl_name = name or os.path.splitext(orig_filename)[0]
        tid = db.add_template(conn, tpl_name, orig_filename, docx_path, html_path,
                              is_active=0 if has_active else 1)

        # автодетект неизвестных полей
        known = {p["key"] for p in db.list_placeholders(conn)}
        unknown = [k for k in collect_keys_from_template(conn, db.get_template(conn, tid))
                   if k not in known]
        if unknown:
            flash("Найдены неизвестные поля: " + ", ".join(unknown) +
                  ". Настройте их на странице /placeholders", "success")
        else:
            flash("Шаблон загружен.", "success")
        return redirect(url_for("templates_page"))
    finally:
        conn.close()


@app.route("/templates/<int:tid>/activate", methods=["POST"])
def activate_template(tid):
    conn = get_conn()
    try:
        if db.get_template(conn, tid) is None:
            flash("Шаблон не найден.", "error")
        else:
            db.activate_template(conn, tid)
            flash("Шаблон активирован.", "success")
        return redirect(url_for("templates_page"))
    finally:
        conn.close()


@app.route("/templates/<int:tid>/delete", methods=["POST"])
def delete_template_route(tid):
    conn = get_conn()
    try:
        tpl = db.get_template(conn, tid)
        if tpl is None:
            flash("Шаблон не найден.", "error")
            return redirect(url_for("templates_page"))
        for path in (tpl["docx_path"], tpl["html_path"]):
            if path and os.path.exists(path):
                os.remove(path)
        db.delete_template(conn, tid)
        flash("Шаблон удалён.", "success")
        return redirect(url_for("templates_page"))
    finally:
        conn.close()


@app.route("/templates/<int:tid>/download")
def download_template_source(tid):
    conn = get_conn()
    try:
        tpl = db.get_template(conn, tid)
        if tpl is None or not os.path.exists(tpl["docx_path"]):
            flash("Исходный DOCX не найден.", "error")
            return redirect(url_for("templates_page"))
        return send_file(tpl["docx_path"], as_attachment=True,
                         download_name=os.path.basename(tpl["docx_path"]))
    finally:
        conn.close()


# ---------- Реестр плейсхолдеров ----------

def parse_placeholder_form(form):
    """Валидация формы плейсхолдера. Возвращает (ok, error, fields)."""
    key = form.get("key", "").strip()
    label = form.get("label", "").strip()
    ptype = form.get("type", "text")
    source = form.get("source", "").strip() or None
    required = 1 if form.get("required") else 0
    bold = 1 if form.get("bold") else 0
    sort_raw = form.get("sort_order", "0")
    sort_order = int(sort_raw) if sort_raw.lstrip("-").isdigit() else 0

    if not db.KEY_RE.match(key):
        return False, "Ключ должен состоять из строчных латинских букв, цифр и _; длина 2..31", None
    if not label:
        return False, "Заполните название поля (label).", None
    if ptype not in db.PLACEHOLDER_TYPES:
        return False, "Недопустимый тип поля.", None
    if ptype != "select":
        source = None
    return True, None, dict(key=key, label=label, ptype=ptype, source=source,
                            required=required, bold=bold, sort_order=sort_order)


@app.route("/placeholders")
def placeholders_page():
    conn = get_conn()
    try:
        rows = db.list_placeholders(conn)
        return render_template("placeholder_list.html", placeholders=rows)
    finally:
        conn.close()


@app.route("/placeholders/new", methods=["GET", "POST"])
def placeholder_new():
    conn = get_conn()
    try:
        custom_lists = db.list_custom_lists(conn)
        if request.method == "POST":
            ok, err, f = parse_placeholder_form(request.form)
            if not ok:
                flash(err, "error")
                return render_template("placeholder_form.html", ph=None,
                                       custom_lists=custom_lists,
                                       form=request.form)
            try:
                db.add_placeholder(conn, f["key"], f["label"], f["ptype"],
                                   f["source"], f["required"], f["bold"], f["sort_order"])
            except db.sqlite3.IntegrityError:
                flash(f"Поле с ключом «{f['key']}» уже существует.", "error")
                return render_template("placeholder_form.html", ph=None,
                                       custom_lists=custom_lists,
                                       form=request.form)
            flash("Поле добавлено.", "success")
            return redirect(url_for("placeholders_page"))
        return render_template("placeholder_form.html", ph=None,
                               custom_lists=custom_lists, form=None)
    finally:
        conn.close()


@app.route("/placeholders/<int:pid>/edit", methods=["GET", "POST"])
def placeholder_edit(pid):
    conn = get_conn()
    try:
        ph = db.get_placeholder(conn, pid)
        if ph is None:
            flash("Поле не найдено.", "error")
            return redirect(url_for("placeholders_page"))
        custom_lists = db.list_custom_lists(conn)
        if request.method == "POST":
            ok, err, f = parse_placeholder_form(request.form)
            if not ok:
                flash(err, "error")
                return render_template("placeholder_form.html", ph=ph,
                                       custom_lists=custom_lists,
                                       form=request.form)
            try:
                db.update_placeholder(conn, pid, f["key"], f["label"], f["ptype"],
                                      f["source"], f["required"], f["bold"], f["sort_order"])
            except db.sqlite3.IntegrityError:
                flash(f"Поле с ключом «{f['key']}» уже существует.", "error")
                return render_template("placeholder_form.html", ph=ph,
                                       custom_lists=custom_lists,
                                       form=request.form)
            flash("Поле обновлено.", "success")
            return redirect(url_for("placeholders_page"))
        return render_template("placeholder_form.html", ph=ph,
                               custom_lists=custom_lists, form=None)
    finally:
        conn.close()


@app.route("/placeholders/<int:pid>/delete", methods=["POST"])
def placeholder_delete(pid):
    conn = get_conn()
    try:
        db.delete_placeholder(conn, pid)
        flash("Поле удалено. Если оно есть в шаблоне — будет отображаться как незарегистрированное.", "success")
        return redirect(url_for("placeholders_page"))
    finally:
        conn.close()


# ---------- Справочники ----------

@app.route("/lists")
def lists_page():
    conn = get_conn()
    try:
        return render_template("lists.html",
                               persons=db.list_persons(conn),
                               addresses=db.list_addresses(conn),
                               custom_lists=db.list_custom_lists(conn))
    finally:
        conn.close()


@app.route("/lists/persons", methods=["GET", "POST"])
def persons_page():
    conn = get_conn()
    try:
        if request.method == "POST":
            full_name = request.form.get("full_name", "").strip()
            if not full_name:
                flash("Введите ФИО.", "error")
            else:
                try:
                    db.add_person(conn, full_name)
                    flash("ФИО добавлено.", "success")
                except db.sqlite3.IntegrityError:
                    flash(f"ФИО «{full_name}» уже существует.", "error")
            return redirect(url_for("persons_page"))
        return render_template("persons.html", persons=db.list_persons(conn))
    finally:
        conn.close()


@app.route("/lists/persons/<int:rid>/delete", methods=["POST"])
def person_delete(rid):
    conn = get_conn()
    try:
        db.delete_person(conn, rid)
        flash("Запись удалена.", "success")
        return redirect(url_for("persons_page"))
    finally:
        conn.close()


@app.route("/lists/addresses", methods=["GET", "POST"])
def addresses_page():
    conn = get_conn()
    try:
        if request.method == "POST":
            address = request.form.get("address", "").strip()
            if not address:
                flash("Введите адрес.", "error")
            else:
                try:
                    db.add_address(conn, address)
                    flash("Адрес добавлен.", "success")
                except db.sqlite3.IntegrityError:
                    flash(f"Адрес «{address}» уже существует.", "error")
            return redirect(url_for("addresses_page"))
        return render_template("addresses.html", addresses=db.list_addresses(conn))
    finally:
        conn.close()


@app.route("/lists/addresses/<int:rid>/delete", methods=["POST"])
def address_delete(rid):
    conn = get_conn()
    try:
        db.delete_address(conn, rid)
        flash("Запись удалена.", "success")
        return redirect(url_for("addresses_page"))
    finally:
        conn.close()


CUSTOM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]+$")


@app.route("/lists/custom/new", methods=["GET", "POST"])
def custom_list_new():
    conn = get_conn()
    try:
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if not CUSTOM_NAME_RE.match(name):
                flash("Название справочника: латиница, с буквы, без пробелов (^[a-z][a-z0-9_]+$).", "error")
                return render_template("custom_list_new.html")
            try:
                lid = db.add_custom_list(conn, name)
            except db.sqlite3.IntegrityError:
                flash(f"Справочник «{name}» уже существует.", "error")
                return redirect(url_for("lists_page"))
            flash("Справочник создан.", "success")
            return redirect(url_for("custom_list_page", lid=lid))
        return render_template("custom_list_new.html")
    finally:
        conn.close()


@app.route("/lists/custom/<int:lid>", methods=["GET", "POST"])
def custom_list_page(lid):
    conn = get_conn()
    try:
        cl = db.get_custom_list(conn, lid)
        if cl is None:
            flash("Справочник не найден.", "error")
            return redirect(url_for("lists_page"))
        if request.method == "POST":
            value = request.form.get("value", "").strip()
            if not value:
                flash("Введите значение.", "error")
            elif db.list_custom_values(conn, lid) and any(
                    v["value"] == value for v in db.list_custom_values(conn, lid)):
                flash(f"Значение «{value}» уже есть в справочнике.", "error")
            else:
                db.add_custom_value(conn, lid, value)
                flash("Значение добавлено.", "success")
            return redirect(url_for("custom_list_page", lid=lid))
        return render_template("custom_list.html", cl=cl,
                               values=db.list_custom_values(conn, lid))
    finally:
        conn.close()


@app.route("/lists/custom/values/<int:vid>/delete", methods=["POST"])
def custom_value_delete(vid):
    conn = get_conn()
    try:
        row = conn.execute("SELECT list_id FROM custom_list_values WHERE id=?", (vid,)).fetchone()
        db.delete_custom_value(conn, vid)
        if row:
            return redirect(url_for("custom_list_page", lid=row["list_id"]))
        return redirect(url_for("lists_page"))
    finally:
        conn.close()


@app.route("/lists/custom/<int:lid>/delete", methods=["POST"])
def custom_list_delete(lid):
    conn = get_conn()
    try:
        db.delete_custom_list(conn, lid)  # значения удалятся каскадом
        flash("Справочник удалён вместе со значениями.", "success")
        return redirect(url_for("lists_page"))
    finally:
        conn.close()


# ---------- Обработчики ошибок ----------

@app.errorhandler(413)
def too_large(e):
    flash("Файл слишком большой (максимум 10 МБ).", "error")
    return redirect(url_for("templates_page"))


ensure_upload_dir()
db.init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
