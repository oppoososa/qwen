# tests/test_routes.py — проверка HTTP-уровня через Flask test_client
import io
import json
import os

import pytest

from conftest import make_docx

import database as db


@pytest.fixture()
def sample_docx_bytes(tmp_path):
    path = make_docx(str(tmp_path / "tpl.docx"), [
        "ФИО:   {{person}}",
        "ДАТА:  {{date1}}",
        "АДРЕС: {{address}}",
        "ДАТА2: {{date2}}",
        "Телефон: {{phone}}",
    ])
    with open(path, "rb") as f:
        return io.BytesIO(f.read())


def upload_template(client, docx_bytes, name="Анкета"):
    data = {"file": (docx_bytes, "template.docx"), "name": name}
    return client.post("/templates/upload", data=data,
                       content_type="multipart/form-data", follow_redirects=True)


# ---------- главная ----------

def test_index_empty_state(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Загрузить первый шаблон" in resp.get_data(as_text=True)


# ---------- placeholders ----------

def test_placeholders_page_has_seed(client):
    resp = client.get("/placeholders")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for key in ("person", "date1", "address", "date2"):
        assert key in body


def test_placeholder_create_and_duplicate(client, app_env):
    resp = client.post("/placeholders/new", data={
        "key": "phone", "label": "Телефон", "type": "text"}, follow_redirects=False)
    assert resp.status_code == 302
    conn = db.get_connection()
    try:
        assert db.get_placeholder_by_key(conn, "phone") is not None
    finally:
        conn.close()

    # повторный POST с тем же key -> 200 + flash-ошибка, запись не создана
    resp2 = client.post("/placeholders/new", data={
        "key": "phone", "label": "Телефон 2", "type": "text"})
    assert resp2.status_code == 200
    assert "уже существует" in resp2.get_data(as_text=True)
    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT * FROM placeholders WHERE key='phone'").fetchall()
        assert len(rows) == 1 and rows[0]["label"] == "Телефон"
    finally:
        conn.close()


def test_placeholder_invalid_key(client):
    resp = client.post("/placeholders/new", data={
        "key": "1abc", "label": "Bad", "type": "text"})
    assert resp.status_code == 200
    assert "Ключ должен состоять" in resp.get_data(as_text=True)


# ---------- lists ----------

def test_persons_page_and_add_and_duplicate(client):
    resp = client.get("/lists/persons")
    assert resp.status_code == 200
    client.post("/lists/persons", data={"full_name": "Иванов И.И."},
                follow_redirects=True)
    resp2 = client.post("/lists/persons", data={"full_name": "Иванов И.И."})
    body = client.get("/lists/persons", follow_redirects=True).get_data(as_text=True)
    assert "Иванов И.И." in body
    # дубликат обработан без падения (flash на следующем запросе)
    assert resp2.status_code == 302


# ---------- templates upload ----------

def test_upload_creates_record_and_files(client, app_env, sample_docx_bytes):
    resp = upload_template(client, sample_docx_bytes)
    assert resp.status_code == 200
    conn = db.get_connection()
    try:
        tpl = db.get_active_template(conn)
        assert tpl is not None
        assert tpl["name"] == "Анкета"
        uploads_dir = app_env.config.get("_unused", None)
        assert os.path.exists(tpl["docx_path"])
        assert os.path.exists(tpl["html_path"])
    finally:
        conn.close()
    # автодетект неизвестного поля phone
    assert "phone" in resp.get_data(as_text=True)


def test_index_after_upload_has_widgets(client, sample_docx_bytes):
    upload_template(client, sample_docx_bytes)
    body = client.get("/").get_data(as_text=True)
    assert '<select name="person"' in body
    assert '<input type="date" name="date1"' in body
    # незарегистрированное поле phone
    assert 'name="phone" class="form-input unregistered"' in body


def test_select_options_from_source(client, sample_docx_bytes):
    client.post("/lists/persons", data={"full_name": "Иванов И.И."}, follow_redirects=True)
    upload_template(client, sample_docx_bytes)
    body = client.get("/").get_data(as_text=True)
    assert "<option value=\"Иванов И.И.\">" in body


# ---------- save / load ----------

def test_save_creates_and_updates_form(client, sample_docx_bytes):
    upload_template(client, sample_docx_bytes)
    form_data = {
        "person": "Иванов И.И.",
        "date1": "2025-01-01",
        "address": "",
        "date2": "",
        "phone": "+79001234567",
        "form_id": "",
    }
    resp = client.post("/save", data=form_data, follow_redirects=False)
    assert resp.status_code == 302
    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT * FROM forms").fetchall()
        assert len(rows) == 1
        data = json.loads(rows[0]["data_json"])
        assert data["person"] == "Иванов И.И."
        assert data["phone"] == "+79001234567"
        assert rows[0]["display_name"] == "Иванов И.И. — 2025-01-01"
        fid = rows[0]["id"]
    finally:
        conn.close()

    # повторный POST с form_id обновляет ту же запись
    form_data["form_id"] = str(fid)
    form_data["date1"] = "2025-02-02"
    client.post("/save", data=form_data, follow_redirects=False)
    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT * FROM forms").fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0]["data_json"])["date1"] == "2025-02-02"
    finally:
        conn.close()

    # открытие сохранённой формы подставляет значения
    page = client.get(f"/form/{fid}").get_data(as_text=True)
    assert 'value="Иванов И.И." selected>' in page or "selected>" in page
    assert 'value="2025-01-01"' not in page  # обновлённая дата
    assert 'value="2025-02-02"' in page


def test_load_page_lists_forms(client, sample_docx_bytes):
    upload_template(client, sample_docx_bytes)
    client.post("/save", data={"person": "Петров П.П.", "date1": "2025-03-03",
                               "address": "", "date2": "", "phone": "",
                               "form_id": ""}, follow_redirects=True)
    body = client.get("/load").get_data(as_text=True)
    assert "Петров П.П. — 2025-03-03" in body


def test_delete_form_route(client, sample_docx_bytes):
    upload_template(client, sample_docx_bytes)
    client.post("/save", data={"person": "Сидоров С.С.", "date1": "", "address": "",
                               "date2": "", "phone": "", "form_id": ""},
                follow_redirects=True)
    conn = db.get_connection()
    fid = conn.execute("SELECT id FROM forms").fetchone()["id"]
    conn.close()
    client.post(f"/form/{fid}/delete", follow_redirects=True)
    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) c FROM forms").fetchone()["c"] == 0
    conn.close()


# ---------- скачивание DOCX ----------

def test_download_docx(client, sample_docx_bytes):
    upload_template(client, sample_docx_bytes)
    resp = client.post("/download_docx", data={
        "person": "Иванов И.И.", "date1": "2025-01-01", "address": "",
        "date2": "", "phone": "+79001234567"})
    assert resp.status_code == 200
    assert "attachment" in resp.headers.get("Content-Disposition", "")
    # файл — валидный zip (DOCX)
    blob = resp.get_data()
    assert blob[:2] == b"PK"
