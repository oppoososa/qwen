# tests/test_database.py — проверка уровня БД
import json

import pytest

import database as db


# ---------- persons / addresses ----------

def test_persons_crud(conn):
    pid = db.add_person(conn, "Иванов И.И.")
    rows = db.list_persons(conn)
    assert [r["full_name"] for r in rows] == ["Иванов И.И."]
    db.delete_person(conn, pid)
    assert db.list_persons(conn) == []


def test_persons_duplicate_raises(conn):
    db.add_person(conn, "Петров П.П.")
    with pytest.raises(db.sqlite3.IntegrityError):
        db.add_person(conn, "Петров П.П.")


def test_addresses_crud(conn):
    aid = db.add_address(conn, "ул. Ленина, 1")
    assert [r["address"] for r in db.list_addresses(conn)] == ["ул. Ленина, 1"]
    with pytest.raises(db.sqlite3.IntegrityError):
        db.add_address(conn, "ул. Ленина, 1")
    db.delete_address(conn, aid)
    assert db.list_addresses(conn) == []


# ---------- custom lists + каскад ----------

def test_custom_list_cascade(conn):
    lid = db.add_custom_list(conn, "dolzhnosti")
    db.add_custom_value(conn, lid, "Директор")
    db.add_custom_value(conn, lid, "Бухгалтер")
    vals = db.list_custom_values(conn, lid)
    assert {v["value"] for v in vals} == {"Директор", "Бухгалтер"}
    db.delete_custom_list(conn, lid)
    # каскад ON DELETE CASCADE должен удалить значения
    assert db.list_custom_values(conn, lid) == []
    assert conn.execute("SELECT COUNT(*) c FROM custom_list_values").fetchone()["c"] == 0


def test_custom_list_unique_name(conn):
    db.add_custom_list(conn, "spisok1")
    with pytest.raises(db.sqlite3.IntegrityError):
        db.add_custom_list(conn, "spisok1")


# ---------- placeholders ----------

def test_seed_placeholders_created(conn):
    rows = db.list_placeholders(conn)
    keys = [r["key"] for r in rows]
    assert keys == ["person", "date1", "address", "date2"]
    person = rows[0]
    assert person["type"] == "select" and person["source"] == "persons" and person["bold"] == 1


def test_init_db_does_not_overwrite(conn):
    # удалим один сид и добавим свой; повторный init_db не должен восстанавливать 4 сида
    db.delete_placeholder(conn, db.get_placeholder_by_key(conn, "date2")["id"])
    db.add_placeholder(conn, "phone", "Телефон", "text")
    conn.execute("DELETE FROM placeholders WHERE key='person'")
    conn.commit()
    db.init_db()  # таблица непустая -> сид не повторяется
    keys = {r["key"] for r in db.list_placeholders(conn)}
    assert "phone" in keys and "person" not in keys


def test_placeholder_crud(conn):
    pid = db.add_placeholder(conn, "phone", "Телефон", "text", bold=1)
    ph = db.get_placeholder(conn, pid)
    assert ph["key"] == "phone" and ph["bold"] == 1
    db.update_placeholder(conn, pid, "phone", "Тел.", "number", None, 1, 0, 5)
    ph = db.get_placeholder(conn, pid)
    assert ph["label"] == "Тел." and ph["type"] == "number" and ph["sort_order"] == 5
    db.delete_placeholder(conn, pid)
    assert db.get_placeholder(conn, pid) is None


def test_placeholder_duplicate_key(conn):
    db.add_placeholder(conn, "phone", "Телефон", "text")
    with pytest.raises(db.sqlite3.IntegrityError):
        db.add_placeholder(conn, "phone", "Телефон 2", "text")


@pytest.mark.parametrize("bad_key", ["1abc", "a-b", "", "AB", "a" * 40])
def test_placeholder_key_validation(bad_key):
    assert db.KEY_RE.match(bad_key) is None


def test_placeholder_key_valid():
    assert db.KEY_RE.match("phone")
    assert db.KEY_RE.match("a1")
    assert db.KEY_RE.match("my_field_2")


# ---------- forms ----------

def test_form_save_read_update(conn):
    data = {"person": "Иванов И.И.", "date1": "2025-01-01"}
    j = json.dumps(data, ensure_ascii=False)
    fid = db.save_form(conn, j, template_id=None, display_name="Иванов И.И. — 2025-01-01")
    row = db.get_form(conn, fid)
    assert json.loads(row["data_json"]) == data
    assert row["display_name"] == "Иванов И.И. — 2025-01-01"

    data2 = dict(data, date1="2025-02-02")
    j2 = json.dumps(data2, ensure_ascii=False)
    again = db.save_form(conn, j2, form_id=fid, display_name="Иванов И.И. — 2025-02-02")
    assert again == fid
    row = db.get_form(conn, fid)
    assert json.loads(row["data_json"])["date1"] == "2025-02-02"

    db.delete_form(conn, fid)
    assert db.get_form(conn, fid) is None
