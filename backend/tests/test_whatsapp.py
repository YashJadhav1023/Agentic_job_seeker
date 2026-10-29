"""WhatsApp via Evolution Go: numbers from files, guarded sending, link flow, opt-out, upload and webhook endpoints."""
import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hub"))
TOKEN = "t" * 40
os.environ["JOBHUB_API_TOKEN"] = TOKEN

import autopilot  # noqa: E402
import core  # noqa: E402
import server  # noqa: E402
import whatsapp  # noqa: E402

H = {"Authorization": f"Bearer {TOKEN}"}


class Resp:
    def __init__(self, body, status=200):
        self._b, self.status_code, self.text = body, status, json.dumps(body)

    def json(self):
        return self._b


class FakeGateway:
    """Stands in for requests.request; records calls and answers like Evolution Go."""

    def __init__(self, logged_in=True, on_whatsapp=None):
        self.calls, self.logged_in = [], logged_in
        self.on_whatsapp = on_whatsapp

    def __call__(self, method, url, headers=None, timeout=None, json=None, files=None, data=None):
        path = url.split("//", 1)[1].split("/", 1)[1]
        self.calls.append((method, "/" + path, headers, json, data, files))
        if path == "instance/create":
            return Resp({"message": "success", "data": {}})
        if path == "instance/status":
            return Resp({"message": "success", "data": {"Connected": self.logged_in, "LoggedIn": self.logged_in, "Name": "Yash"}})
        if path == "instance/connect":
            return Resp({"message": "success"})
        if path == "instance/qr":
            return Resp({"message": "success", "data": {"qrcode": "data:image/png;base64,AAAA|2@abc", "code": "2@abc"}})
        if path == "user/check":
            nums = json["number"]
            ok = self.on_whatsapp if self.on_whatsapp is not None else nums
            return Resp({"message": "success", "data": {"Users": [{"Query": n, "IsInWhatsapp": n in ok} for n in nums]}})
        if path == "send/media":
            return Resp({"message": "success", "data": {"id": "m1"}})
        return Resp({"error": "not found"}, 404)


@pytest.fixture
def env(tmp_path, monkeypatch):
    src = (Path(__file__).resolve().parent.parent / "profile.example.yaml").read_text()
    src = src.replace("/app/data/resume.pdf", str(tmp_path / "resume.pdf"))
    f = tmp_path / "profile.yaml"
    f.write_text(src)
    (tmp_path / "resume.pdf").write_bytes(b"%PDF-1.4 resume")
    (tmp_path / "secrets").mkdir()
    monkeypatch.setattr(core, "PROFILE_PATH", f)
    monkeypatch.setattr(core, "SECRETS", tmp_path / "secrets")
    monkeypatch.setenv("JOBHUB_EVOLUTION_URL", "http://evolution-go:4000")
    monkeypatch.setenv("JOBHUB_EVOLUTION_KEY", "k" * 64)
    monkeypatch.setattr(autopilot, "stopped", lambda: False)
    monkeypatch.setattr(autopilot, "in_send_window", lambda c, now=None: True)
    return tmp_path


def allow_live(monkeypatch, confirmed=True):
    monkeypatch.setattr(core, "real_actions_allowed", lambda p: (True, ""))
    whatsapp.save_settings({"recipients_expect_my_message": confirmed, "daily_cap": 5, "delay_sec": [5, 5]})


def add(*numbers):
    whatsapp.import_numbers("\n".join(numbers))


# ---------------------------------------------------------------- numbers
@pytest.mark.parametrize("raw,expected", [
    ("98765 43210", "919876543210"), ("+91 98765-43210", "919876543210"), ("09876543210", "919876543210"),
    ("+1 (415) 555-0132", "14155550132"), ("00447911123456", "447911123456"), ("919876543210", "919876543210"),
    ("12345", None), ("5555555555", None), ("+91 12345 67890", None), ("abc", None),
])
def test_normalize(raw, expected):
    assert whatsapp.normalize(raw, "91") == expected


def test_extract_numbers_with_names_from_lines_tables_and_csv():
    text = "Priya Sharma | HR | 98765 43210\nRahul,+91 99887 76655,rahul@acme.com\nCall 98765 43210 again\nno number here\nOrder 20261028"
    got = whatsapp.extract_numbers(text, "91")
    assert [g["number"] for g in got] == ["919876543210", "919988776655"]       # duplicate removed, order number ignored
    assert got[0]["name"].startswith("Priya Sharma") and got[1]["name"] == "Rahul"


def _docx(lines, table=None):
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    paras = "".join(f"<w:p><w:r><w:t>{l}</w:t></w:r></w:p>" for l in lines)
    tbl = ""
    for row in table or []:
        tbl += "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row) + "</w:tr>"
    xml = f'<?xml version="1.0"?><w:document {ns}><w:body>{paras}<w:tbl>{tbl}</w:tbl></w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_word_file_with_paragraphs_and_a_table_keeps_name_and_number_together():
    data = _docx(["Hiring contacts", "Anita Rao 98111 22233"], table=[["Vikram Singh", "98222 33344"]])
    nums = whatsapp.extract_numbers(whatsapp.file_to_text(data, "list.docx"), "91")
    by = {n["number"]: n["name"] for n in nums}
    assert by["919811122233"].startswith("Anita Rao") and by["919822233344"] == "Vikram Singh"


def test_excel_pdf_csv_and_text_files():
    import fitz
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.append(["Name", "Phone"])
    wb.active.append(["Neha", 9876543210])
    buf = io.BytesIO()
    wb.save(buf)
    assert whatsapp.extract_numbers(whatsapp.file_to_text(buf.getvalue(), "x.xlsx"), "91")[0]["number"] == "919876543210"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Recruiter Sam +91 98765 11111")
    assert whatsapp.extract_numbers(whatsapp.file_to_text(doc.tobytes(), "x.pdf"), "91")[0]["number"] == "919876511111"
    assert whatsapp.extract_numbers(whatsapp.file_to_text(b"A,9876500000\n", "x.csv"))[0]["name"] == "A"
    with pytest.raises(ValueError):
        whatsapp.file_to_text(b"x", "old.doc")
    with pytest.raises(ValueError):
        whatsapp.file_to_text(b"x" * (9 * 1024 * 1024), "big.txt")


def test_unsafe_word_xml_and_bad_zip_are_rejected():
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("word/document.xml", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>')
    with pytest.raises(ValueError):
        whatsapp.file_to_text(evil.getvalue(), "x.docx")
    with pytest.raises(Exception):
        whatsapp.file_to_text(b"not a zip", "x.docx")


def test_import_is_idempotent_and_respects_optouts(env):
    first = whatsapp.import_numbers("Asha 98765 43210\nBen 98765 43211")
    assert first["new"] == 2
    again = whatsapp.import_numbers("98765 43210")
    assert again["new"] == 0 and again["already_known"] == 1
    with core.db() as c:
        c.execute("INSERT INTO suppression(key,reason,at) VALUES('wa:919876543299','stop','now')")
    assert whatsapp.import_numbers("98765 43299")["blocked"] == 1


# ---------------------------------------------------------------- link / status
def test_link_creates_instance_once_connects_and_returns_the_qr(env):
    gw = FakeGateway(logged_in=False)
    r = whatsapp.link(whatsapp.Evo(http=gw))
    assert r["state"] == "scan" and r["qr"].startswith("data:image/png;base64,")
    paths = [c[1] for c in gw.calls]
    assert paths[0] == "/instance/create" and "/instance/connect" in paths and paths[-1] == "/instance/qr"
    create = gw.calls[0]
    assert create[2]["apikey"] == "k" * 64                       # admin key only for create
    import uuid
    assert str(uuid.UUID(create[3]["instanceId"])) == create[3]["instanceId"] and create[3]["name"] == "jobseeker"   # the gateway needs a UUID id
    assert core.kv_get("whatsapp:instance")["id"] == create[3]["instanceId"]
    assert gw.calls[-1][2]["apikey"] == whatsapp.instance_token() != "k" * 64
    connect = next(c for c in gw.calls if c[1] == "/instance/connect")
    assert connect[3]["webhookUrl"].startswith("http://jobhub:8000/api/v1/whatsapp/webhook?s=") and connect[3]["subscribe"] == ["MESSAGE"]
    gw2 = FakeGateway(logged_in=False)
    whatsapp.link(whatsapp.Evo(http=gw2))
    assert "/instance/create" not in [c[1] for c in gw2.calls]   # not created twice


def test_link_polling_does_not_reconnect_and_reset_the_qr(env, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(whatsapp.time, "time", lambda: clock[0])
    gw = FakeGateway(logged_in=False)
    evo = whatsapp.Evo(http=gw)
    for _ in range(5):                      # the app polls every 4 s
        whatsapp.link(evo)
        clock[0] += 4
    assert [c[1] for c in gw.calls].count("/instance/connect") == 1
    clock[0] += whatsapp.CONNECT_EVERY
    whatsapp.link(evo)
    assert [c[1] for c in gw.calls].count("/instance/connect") == 2   # retried after the interval


def test_link_does_not_connect_when_gateway_already_connected(env):
    class Waiting(FakeGateway):
        def __call__(self, method, url, **kw):
            if url.endswith("/instance/status"):
                self.calls.append((method, "/instance/status", None, None, None, None))
                return Resp({"data": {"Connected": True, "LoggedIn": False}})
            return super().__call__(method, url, **kw)
    gw = Waiting()
    assert whatsapp.link(whatsapp.Evo(http=gw))["state"] == "scan"
    assert "/instance/connect" not in [c[1] for c in gw.calls]


def test_link_reports_waiting_while_no_qr_yet(env):
    class NoQr(FakeGateway):
        def __call__(self, method, url, **kw):
            if url.endswith("/instance/qr"):
                return Resp({"error": "no QR code available"}, 400)
            return super().__call__(method, url, **kw)
    r = whatsapp.link(whatsapp.Evo(http=NoQr(logged_in=False)))
    assert r["state"] == "waiting" and "error" not in r


def test_link_when_already_linked_and_when_not_configured(env, monkeypatch):
    assert whatsapp.link(whatsapp.Evo(http=FakeGateway(logged_in=True))) == {"state": "linked", "name": "Yash"}
    monkeypatch.delenv("JOBHUB_EVOLUTION_URL")
    assert "not configured" in whatsapp.link()["error"]
    assert whatsapp.status()["configured"] is False and "hint" in whatsapp.status()


def test_secret_files_are_private_and_stable(env):
    t = whatsapp.instance_token()
    assert t == whatsapp.instance_token() and len(t) >= 32
    assert oct((core.SECRETS / "whatsapp_instance_token").stat().st_mode & 0o777) == "0o600"


# ---------------------------------------------------------------- sending rules
def test_preview_is_the_default_and_sends_nothing(env):
    add("Asha 98765 43210")
    r = server.whatsapp_send({})
    assert r["dry_run"] is True and r["would_send"] == 1
    assert "Hi Asha" in r["drafts"][0]["message"] and "Reply STOP" in r["drafts"][0]["message"]
    assert whatsapp.sent_today() == 0


def test_live_send_needs_the_switch_and_the_confirmation(env, monkeypatch):
    add("98765 43210")
    monkeypatch.setattr(core, "real_actions_allowed", lambda p: (False, "confirmed_by_user is false"))
    assert "blocked" in server.whatsapp_send({"dry_run": False})["error"]
    monkeypatch.setattr(core, "real_actions_allowed", lambda p: (True, ""))
    assert "confirm first" in server.whatsapp_send({"dry_run": False})["error"]
    assert "confirm first" in whatsapp.send_batch(evo=whatsapp.Evo(http=FakeGateway()))["error"]


def test_send_attaches_the_resume_with_the_message_and_records_it(env, monkeypatch):
    allow_live(monkeypatch)
    add("Asha 98765 43210")
    gw = FakeGateway()
    sleeps = []
    r = whatsapp.send_batch(evo=whatsapp.Evo(http=gw), sleep=sleeps.append, log=lambda m: None)
    assert r["sent"] == 1 and whatsapp.sent_today() == 1 and whatsapp.counts() == {"sent": 1}
    media = next(c for c in gw.calls if c[1] == "/send/media")
    assert media[4]["type"] == "document" and media[4]["number"] == "919876543210" and "Hi Asha" in media[4]["caption"]
    assert media[5]["file"][0].endswith(".pdf")


def test_daily_cap_pause_and_not_on_whatsapp(env, monkeypatch):
    allow_live(monkeypatch)                                       # cap 5, pause 5 s
    add(*[f"98765 4321{i}" for i in range(8)])
    gw = FakeGateway(on_whatsapp=[f"91987654321{i}" for i in range(1, 7)])     # numbers 0 and 7 are not on WhatsApp
    sleeps = []
    r = whatsapp.send_batch(limit=20, evo=whatsapp.Evo(http=gw), sleep=sleeps.append, log=lambda m: None)
    assert r["sent"] == 5 and r["skipped_not_on_whatsapp"] == 1       # cap 5 reached with real sends; the unreachable number did not use a slot
    assert len(sleeps) == 4 and all(s == 5 for s in sleeps)           # pauses sit between real sends only
    assert whatsapp.sent_today() == 5
    again = whatsapp.send_batch(limit=20, evo=whatsapp.Evo(http=gw), sleep=lambda s: None, log=lambda m: None)
    assert "daily cap" in again["error"]


def test_nobody_is_messaged_twice_within_30_days(env, monkeypatch):
    allow_live(monkeypatch)
    add("98765 43210")
    gw = FakeGateway()
    whatsapp.send_batch(evo=whatsapp.Evo(http=gw), sleep=lambda s: None, log=lambda m: None)
    core.db().execute("UPDATE wa_contacts SET status='new'").connection.commit()    # even if re-queued by mistake
    r = whatsapp.send_batch(evo=whatsapp.Evo(http=gw), sleep=lambda s: None, log=lambda m: None)
    assert r["sent"] == 0 and "nobody eligible" in r["note"]


def test_kill_switch_window_not_linked_and_missing_resume(env, monkeypatch):
    allow_live(monkeypatch)
    add("98765 43210")
    ev = whatsapp.Evo(http=FakeGateway())
    monkeypatch.setattr(autopilot, "stopped", lambda: True)
    assert "kill-switch" in whatsapp.send_batch(evo=ev)["error"]
    monkeypatch.setattr(autopilot, "stopped", lambda: False)
    monkeypatch.setattr(autopilot, "in_send_window", lambda c, now=None: False)
    assert "send window" in whatsapp.send_batch(evo=ev)["error"]
    monkeypatch.setattr(autopilot, "in_send_window", lambda c, now=None: True)
    assert "not linked" in whatsapp.send_batch(evo=whatsapp.Evo(http=FakeGateway(logged_in=False)))["error"]
    (env / "resume.pdf").unlink()
    assert "no resume" in whatsapp.send_batch(evo=ev)["error"]


def test_hard_cap_cannot_be_exceeded_and_settings_are_validated(env):
    assert whatsapp.save_settings({"daily_cap": 500})["daily_cap"] == 50
    with pytest.raises(ValueError):
        whatsapp.save_settings({"delay_sec": [1, 2]})
    with pytest.raises(ValueError):
        whatsapp.save_settings({"mode": "yolo"})
    assert whatsapp.save_settings({"default_country_code": "+44"})["default_country_code"] == "44"


# ---------------------------------------------------------------- replies
def _msg(number, text, group=False, me=False):
    return {"event": "Message", "data": {"Info": {"Sender": f"{number}@s.whatsapp.net", "IsGroup": group, "IsFromMe": me},
                                          "Message": {"conversation": text}}}


def test_stop_reply_opts_out_for_good_and_other_replies_are_recorded(env, monkeypatch):
    add("98765 43210", "98765 43211")
    assert whatsapp.handle_event(_msg("919876543210", "STOP please")) == {"optout": "919876543210"}
    assert whatsapp.handle_event(_msg("919876543211", "Sure, send details")) == {"replied": "919876543211"}
    assert whatsapp.counts() == {"optout": 1, "replied": 1}
    assert whatsapp.import_numbers("98765 43210")["already_known"] == 1
    assert whatsapp._suppressed("919876543210")
    assert whatsapp.handle_event(_msg("919999999999", "hello")) == {"ignored": True}          # not someone we messaged
    assert whatsapp.handle_event(_msg("919876543211", "x", group=True)) == {"ignored": True}   # groups handled elsewhere
    assert whatsapp.handle_event({"event": "Connected", "data": {}}) == {"ignored": True}


# ---------------------------------------------------------------- HTTP endpoints
@pytest.fixture
def client(env, monkeypatch):
    import api
    from fastapi.testclient import TestClient
    monkeypatch.setattr(api, "lockout", api._Lockout(max_fail=3, window=60, ban=60))
    return TestClient(api.create_app())


def test_upload_endpoint_parses_files_and_rejects_bad_ones(client, env):
    assert client.post("/api/v1/whatsapp/numbers", files={"file": ("a.txt", b"Asha 98765 43210")}).status_code == 401
    r = client.post("/api/v1/whatsapp/numbers", headers=H, files={"file": ("a.docx", _docx(["Ben 98765 43211"]))})
    assert r.status_code == 200 and r.json()["new"] == 1
    assert client.post("/api/v1/whatsapp/numbers", headers=H, files={"file": ("a.doc", b"x")}).status_code == 422
    assert client.post("/api/v1/whatsapp/numbers", headers=H, files={"file": ("a.pdf", b"not a pdf")}).status_code == 422


def test_webhook_needs_its_secret(client, env):
    add("98765 43210")
    body = _msg("919876543210", "stop")
    assert client.post("/api/v1/whatsapp/webhook?s=wrong", json=body).status_code == 401
    ok = client.post(f"/api/v1/whatsapp/webhook?s={whatsapp.webhook_secret()}", json=body)
    assert ok.status_code == 200 and ok.json()["optout"] == "919876543210"
    assert client.post(f"/api/v1/whatsapp/webhook?s={whatsapp.webhook_secret()}", content=b"not json").json() == {"ok": True}


def test_tools_and_alarm_task_exist(env):
    import scheduler
    assert {"whatsapp_status", "whatsapp_link", "whatsapp_send", "whatsapp_import_numbers"} <= set(server.HANDLERS)
    assert server.whatsapp_import_numbers({"text": "Asha 98765 43210"})["new"] == 1
    assert server.whatsapp_contacts({})["count"] == 1
    al = scheduler.alarm_set({"time": "10:00", "tasks": ["whatsapp_send"], "dry_run": True})["alarm"]
    assert al["tasks"] == ["whatsapp_send"]
    assert server.whatsapp_settings({"daily_cap": 7})["daily_cap"] == 7


def test_all_daily_tasks_never_includes_whatsapp(env):
    import scheduler
    assert "whatsapp_send" not in scheduler.alarm_set({"time": "08:00", "tasks": ["all"]})["alarm"]["tasks"]
