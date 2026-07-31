#!/usr/bin/env python3
"""
本地翻译审核工作台 (Flask)。

放置位置: <project_root>/webapp/app.py
运行:
    cd webapp
    pip install flask requests
    python app.py
    # 浏览器打开 http://127.0.0.1:5001

复用项目已有的目录约定:
    <project_root>/config.py                  token / project id
    <project_root>/review_decisions.csv        决策表（本工具的核心编辑对象）
    <project_root>/push_receipt.csv            推送日志
    <project_root>/data/excluded_keys.txt      隐藏key
    <project_root>/data/paratranz_terms.csv    术语表
"""
import csv
import io
import json
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.py"
DECISIONS_PATH = ROOT / "review_decisions.csv"
PUSH_LOG_PATH = ROOT / "push_receipt.csv"
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
SCRIPTS_DIR = ROOT / "scripts"
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
DUP_REPORT_PATH = REPORTS_DIR / "inconsistency_duplicate_source.csv"
GLOSSARY_REPORT_PATH = REPORTS_DIR / "inconsistency_glossary.csv"
GLOSSARY_REPORT_FIELDS = ["term_count", "confidence", "term", "matched_source_text",
                          "expected_translation", "key", "source_text",
                          "current_translation", "glossary_note"]

API_BASE = "https://paratranz.cn/api"

DECISION_FIELDS = [
    "row_id", "key", "source_report", "group_or_term", "source_text",
    "current_translation", "suggested_translation", "confidence",
    "decision", "final_translation", "reviewer_note", "excluded",
]

app = Flask(__name__, static_folder="static", template_folder="templates")


# ---------------------------------------------------------------- config ---

def load_config():
    """Read config.py fresh every call (it may have been edited via the UI)."""
    ns = {"PARATRANZ_TOKEN": "", "PROJECT_ID": None}
    if CONFIG_PATH.exists():
        code = CONFIG_PATH.read_text(encoding="utf-8")
        exec(compile(code, str(CONFIG_PATH), "exec"), ns)
    return {"token": ns.get("PARATRANZ_TOKEN") or "", "project_id": ns.get("PROJECT_ID")}


def save_config(token, project_id):
    content = (
        "# 由 webapp 自动生成/更新，勿手动混排其他逻辑\n"
        f"PARATRANZ_TOKEN = {json.dumps(token)}\n"
        f"PROJECT_ID = {int(project_id) if project_id not in (None, '') else 'None'}\n"
    )
    CONFIG_PATH.write_text(content, encoding="utf-8")


@app.get("/api/config")
def api_get_config():
    cfg = load_config()
    token = cfg["token"]
    masked = (token[:4] + "…" + token[-4:]) if len(token) > 8 else ("***" if token else "")
    return jsonify({"project_id": cfg["project_id"], "token_masked": masked, "has_token": bool(token)})


@app.post("/api/config")
def api_save_config():
    body = request.get_json(force=True)
    token = (body.get("token") or "").strip()
    project_id = body.get("project_id")
    # keep existing token if user leaves the field blank (masked display)
    if not token:
        token = load_config()["token"]
    save_config(token, project_id)
    return jsonify({"ok": True})


# ------------------------------------------------------------- fetch API ---

def paratranz_get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": token})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


@app.post("/api/fetch/hidden-keys")
def api_fetch_hidden_keys():
    cfg = load_config()
    if not cfg["token"] or not cfg["project_id"]:
        return jsonify({"ok": False, "error": "请先配置 token 和 project_id"}), 400
    stage = request.get_json(force=True).get("stage", -1)
    page_size = 100
    keys, page = [], 1
    while True:
        url = f"{API_BASE}/projects/{cfg['project_id']}/strings?stage={stage}&page={page}&page_size={page_size}"
        try:
            data = paratranz_get(url, cfg["token"])
        except urllib.error.HTTPError as e:
            return jsonify({"ok": False, "error": f"HTTP {e.code}: {e.reason}"}), 502
        results = data.get("results", [])
        if not results:
            break
        keys.extend(item["key"] for item in results)
        page_count = data.get("pageCount", data.get("page_count", 0))
        if page >= page_count or not results:
            break
        page += 1
        time.sleep(0.3)

    out_path = DATA_DIR / "excluded_keys.txt"
    out_path.write_text("\n".join(keys) + ("\n" if keys else ""), encoding="utf-8")
    return jsonify({"ok": True, "count": len(keys), "path": str(out_path)})


def stringify(value):
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                import ast
                value = ast.literal_eval(s)
            except (ValueError, SyntaxError):
                pass
    if value in (None, ""):
        return ""
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(item.get("term") or item.get("translation") or item.get("value") or str(item))
            else:
                parts.append(str(item))
        return " | ".join(parts)
    if isinstance(value, dict):
        return str(value)
    return str(value)


@app.post("/api/fetch/artifacts")
def api_fetch_artifacts():
    """跑 1_fetch_paratranz_artifacts.py：触发 Paratranz 打包 -> 轮询 -> 下载 zip ->
    解压合并成 data/Merged.csv。打包是异步的，可能要几秒到一两分钟，这里同步等，
    请求会挂起到脚本跑完为止。"""
    cfg = load_config()
    if not cfg["token"] or not cfg["project_id"]:
        return jsonify({"ok": False, "error": "请先配置 token 和 project_id"}), 400

    out_path = DATA_DIR / "Merged.csv"
    cmd = [sys.executable, str(SCRIPTS_DIR / "1_fetch_paratranz_artifacts.py"),
           "--out", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), timeout=360)
    if r.returncode != 0:
        return jsonify({"ok": False, "error": (r.stderr or r.stdout)[-4000:]}), 500

    out_lines = r.stdout.strip().splitlines()
    file_count = key_count = None
    for line in out_lines:
        if line.startswith("完成"):
            # "完成。合并了 N 个 CSV 文件，共 M 条 key，写入 ..."
            import re as _re
            m = _re.search(r"合并了 (\d+) 个 CSV 文件，共 (\d+) 条 key", line)
            if m:
                file_count, key_count = int(m.group(1)), int(m.group(2))
    return jsonify({
        "ok": True, "path": str(out_path),
        "file_count": file_count, "key_count": key_count,
        "log": out_lines[-10:],
    })


@app.post("/api/fetch/terms")
def api_fetch_terms():
    cfg = load_config()
    if not cfg["token"] or not cfg["project_id"]:
        return jsonify({"ok": False, "error": "请先配置 token 和 project_id"}), 400
    fields = ["pos", "uid", "term", "translation", "note", "variants"]
    page, page_size, all_terms = 1, 100, []
    while True:
        url = f"{API_BASE}/projects/{cfg['project_id']}/terms"
        req = urllib.request.Request(
            url + "?" + urllib.parse.urlencode({"page": page, "pageSize": page_size}),
            headers={"Authorization": cfg["token"]},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return jsonify({"ok": False, "error": f"HTTP {e.code}: {e.reason}"}), 502
        if isinstance(data, dict) and "results" in data:
            all_terms.extend(data["results"])
            page_count = data.get("pageCount")
            if page_count is None or page >= page_count or not data["results"]:
                break
            page += 1
            time.sleep(0.2)
        elif isinstance(data, list):
            all_terms.extend(data)
            break
        else:
            return jsonify({"ok": False, "error": f"未识别的返回格式: {data!r}"}), 502

    out_path = DATA_DIR / "paratranz_terms.csv"
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for term in all_terms:
            w.writerow({field: stringify(term.get(field)) for field in fields})
    return jsonify({"ok": True, "count": len(all_terms), "path": str(out_path)})


# -------------------------------------------------------- report regen ----

def _resolve(p):
    pp = Path(p)
    return pp if pp.is_absolute() else (ROOT / pp)


@app.post("/api/reports/regenerate")
def api_regenerate_reports():
    """一键跑 2_check_consistency.py + 3_build_review_sheet.py。

    报告本身照旧写到 reports/ 目录（路径不变），只是把手动跑两条命令这一步
    收进网页；3_build_review_sheet.py 自带"按 key 继承旧决策表 decision /
    final_translation / reviewer_note / excluded"的逻辑，所以这里刷新决策表
    不会丢已经做过的判断和排除标记——前提是那个 key 在新一轮报告里还存在。
    """
    body = request.get_json(force=True) or {}
    source = _resolve(body.get("source") or "data/Original_En.csv")
    translated = _resolve(body.get("translated") or "data/Merged.csv")
    glossary = _resolve(body.get("glossary") or "data/paratranz_terms.csv")
    exclude_keys = _resolve(body.get("exclude_keys") or "data/excluded_keys.txt")

    missing = [str(p) for p in (source, translated) if not p.exists()]
    if missing:
        return jsonify({"ok": False, "error": "找不到原文/译文文件，请先放好这两份 CSV：\n" + "\n".join(missing)}), 400

    step2 = [sys.executable, str(SCRIPTS_DIR / "2_check_consistency.py"),
             "--source", str(source), "--translated", str(translated),
             "--outdir", str(REPORTS_DIR)]
    if glossary.exists():
        step2 += ["--glossary", str(glossary)]
    if exclude_keys.exists():
        step2 += ["--exclude-keys", str(exclude_keys)]

    r2 = subprocess.run(step2, capture_output=True, text=True, cwd=str(ROOT))
    if r2.returncode != 0:
        return jsonify({"ok": False, "error": "步骤2 (一致性检查) 失败:\n" + (r2.stderr or r2.stdout)[-4000:]}), 500
    if not DUP_REPORT_PATH.exists():
        return jsonify({"ok": False, "error": "步骤2 未生成 duplicate 报告，日志:\n" + r2.stdout[-4000:]}), 500
    if not GLOSSARY_REPORT_PATH.exists():
        # 没配置/没找到术语表也没关系，给 step3 一份空报告，避免它因为缺文件报错
        with open(GLOSSARY_REPORT_PATH, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerow(GLOSSARY_REPORT_FIELDS)

    step3 = [sys.executable, str(SCRIPTS_DIR / "3_build_review_sheet.py"),
             "--duplicate", str(DUP_REPORT_PATH), "--glossary", str(GLOSSARY_REPORT_PATH),
             "--out", str(DECISIONS_PATH)]
    r3 = subprocess.run(step3, capture_output=True, text=True, cwd=str(ROOT))
    if r3.returncode != 0:
        return jsonify({"ok": False, "error": "步骤3 (合并决策表) 失败:\n" + (r3.stderr or r3.stdout)[-4000:]}), 500

    rows = read_decisions()
    return jsonify({
        "ok": True,
        "total_rows": len(rows),
        "step2_log": r2.stdout.strip().splitlines()[-8:],
        "step3_log": r3.stdout.strip().splitlines()[-8:],
        "used_glossary": glossary.exists(),
        "used_exclude_keys": exclude_keys.exists(),
    })


# ---------------------------------------------------------- decisions API --

def read_decisions():
    if not DECISIONS_PATH.exists():
        return []
    with open(DECISIONS_PATH, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r.setdefault("excluded", "")
        if not r.get("row_id"):          # 兼容还没跑过新版 3_build_review_sheet.py 的旧文件
            r["row_id"] = r["key"]
    return rows


def write_decisions(rows):
    with open(DECISIONS_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=DECISION_FIELDS)
        w.writeheader()
        w.writerows(rows)


@app.get("/api/decisions")
def api_get_decisions():
    rows = read_decisions()
    return jsonify({"rows": rows, "total": len(rows)})


@app.post("/api/decisions/bulk-decision")
def api_bulk_decision():
    """Set decision (accept/reject/edit/'') on a batch of keys immediately (no debounce)."""
    body = request.get_json(force=True)
    row_ids = set(body.get("row_ids") or [])
    decision = body.get("decision", "")
    if decision not in ("accept", "reject", "edit", ""):
        return jsonify({"ok": False, "error": "decision 必须是 accept/reject/edit/空"}), 400
    if not row_ids:
        return jsonify({"ok": False, "error": "没有指定要处理的行"}), 400
    rows = read_decisions()
    updated = 0
    for r in rows:
        if r["row_id"] in row_ids:
            r["decision"] = decision
            updated += 1
    write_decisions(rows)
    return jsonify({"ok": True, "updated": updated})


@app.post("/api/decisions/bulk-exclude")
def api_bulk_exclude():
    """Soft-exclude/un-exclude a batch of keys. Excluded rows stay in the sheet,
    remain editable, are visually greyed out and sorted last, but are skipped at
    push time regardless of their decision. Fully reversible — just toggle back."""
    body = request.get_json(force=True)
    row_ids = set(body.get("row_ids") or [])
    excluded = bool(body.get("excluded"))
    if not row_ids:
        return jsonify({"ok": False, "error": "没有指定要处理的行"}), 400
    rows = read_decisions()
    updated = 0
    for r in rows:
        if r["row_id"] in row_ids:
            r["excluded"] = "1" if excluded else ""
            updated += 1
    write_decisions(rows)
    return jsonify({"ok": True, "updated": updated, "excluded": excluded})


@app.post("/api/decisions/save")
def api_save_decisions():
    """Upsert a batch of edited rows (by key). Only decision/final_translation/reviewer_note
    are expected to change from the UI; other columns are left as-is."""
    changes = request.get_json(force=True).get("rows", [])
    if not changes:
        return jsonify({"ok": True, "updated": 0})
    by_row_id = {c["row_id"]: c for c in changes}
    rows = read_decisions()
    updated = 0
    for r in rows:
        c = by_row_id.get(r["row_id"])
        if not c:
            continue
        for field in ("decision", "final_translation", "reviewer_note"):
            if field in c:
                r[field] = c[field]
        updated += 1
    write_decisions(rows)
    return jsonify({"ok": True, "updated": updated})


@app.post("/api/decisions/restore")
def api_restore_decisions():
    """用于前端的一步撤销：把指定 key 的指定字段恢复成给定值。
    跟 /save 不同的是这里按 (key, field, value) 三元组来，而不是固定的三个
    可编辑列，因为撤销可能来自 bulk-decision（只改 decision）、
    bulk-exclude（只改 excluded）或查找替换（改 final_translation/reviewer_note）。"""
    changes = request.get_json(force=True).get("rows", [])
    if not changes:
        return jsonify({"ok": True, "updated": 0})
    allowed = {"decision", "final_translation", "reviewer_note", "excluded"}
    by_row_id = {}
    for c in changes:
        if c.get("field") not in allowed:
            continue
        by_row_id.setdefault(c["row_id"], {})[c["field"]] = c.get("value", "")
    rows = read_decisions()
    updated = 0
    for r in rows:
        fields = by_row_id.get(r["row_id"])
        if not fields:
            continue
        for f, v in fields.items():
            r[f] = v
        updated += 1
    write_decisions(rows)
    return jsonify({"ok": True, "updated": updated})


@app.post("/api/decisions/find-replace")
def api_find_replace():
    """Server-side find/replace across the FULL decision table (not just what's
    loaded in the browser), so it's safe at 8000+ rows."""
    body = request.get_json(force=True)
    field = body.get("field")
    find = body.get("find", "")
    replace = body.get("replace", "")
    use_regex = bool(body.get("use_regex"))
    only_decision = body.get("only_decision")  # optional filter: only rows with this decision value
    row_ids = body.get("row_ids")  # optional list: restrict to these keys (selected / filtered scope from UI)
    dry_run = bool(body.get("dry_run", True))

    if field not in ("final_translation", "reviewer_note"):
        return jsonify({"ok": False, "error": "只允许对 final_translation / reviewer_note 做批量替换"}), 400
    if not find:
        return jsonify({"ok": False, "error": "查找内容不能为空"}), 400

    row_id_set = set(row_ids) if row_ids is not None else None

    rows = read_decisions()
    matches = []
    for r in rows:
        if row_id_set is not None and r["row_id"] not in row_id_set:
            continue
        if only_decision and r.get("decision", "") != only_decision:
            continue
        text = r.get(field, "")
        if use_regex:
            try:
                new_text, n = re.subn(find, replace, text)
            except re.error as e:
                return jsonify({"ok": False, "error": f"正则错误: {e}"}), 400
        else:
            n = text.count(find)
            new_text = text.replace(find, replace)
        if n:
            matches.append({"row_id": r["row_id"], "before": text, "after": new_text})
            if not dry_run:
                r[field] = new_text

    if not dry_run and matches:
        write_decisions(rows)

    return jsonify({"ok": True, "match_count": len(matches), "preview": matches[:50], "applied": not dry_run})


# ---------------------------------------------------------------- push API -

def normalize_key(k):
    return unicodedata.normalize("NFC", k.strip())


def find_id_by_key(project_id, key, token):
    url = f"{API_BASE}/projects/{project_id}/strings?key={urllib.parse.quote(key)}"
    data = paratranz_get(url, token)
    for item in data.get("results", []):
        if normalize_key(item["key"]) == normalize_key(key):
            return item["id"]
    return None


def patch_one(project_id, string_id, new_translation, token):
    url = f"{API_BASE}/projects/{project_id}/strings/{string_id}"
    body = json.dumps({"translation": new_translation}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="PUT",
                                 headers={"Authorization": token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


@app.post("/api/push")
def api_push():
    cfg = load_config()
    if not cfg["token"] or not cfg["project_id"]:
        return jsonify({"ok": False, "error": "请先配置 token 和 project_id"}), 400
    body = request.get_json(force=True)
    dry_run = bool(body.get("dry_run", True))

    rows = read_decisions()
    patch_rows = []
    skipped_excluded = 0
    for r in rows:
        if r.get("excluded"):
            if (r.get("decision") or "").strip().lower() in ("accept", "edit"):
                skipped_excluded += 1
            continue
        decision = (r.get("decision") or "").strip().lower()
        if decision == "accept":
            new_t = r.get("suggested_translation", "")
        elif decision == "edit":
            new_t = r.get("final_translation", "")
        else:
            continue
        if not new_t:
            continue
        patch_rows.append({"row_id": r["row_id"], "key": r["key"], "new_translation": new_t,
                           "old_translation": r.get("current_translation", "")})

    # 同一个 key 现在可能对应多行（多个不同问题）。如果它们都待推送但给出的
    # 译文不一样，就是冲突——不能瞎选一个提交，必须让人工去决策表里把其中
    # 一行的 decision 改掉/排除掉，再重新推送。
    by_key = {}
    for row in patch_rows:
        by_key.setdefault(row["key"], []).append(row)
    conflicts = {k: v for k, v in by_key.items() if len({x["new_translation"] for x in v}) > 1}
    if conflicts:
        conflict_rows = [r for rs in conflicts.values() for r in rs]
        patch_rows = [r for r in patch_rows if r["key"] not in conflicts]
    else:
        conflict_rows = []

    log_rows = []
    ok_keys = {}
    for row in patch_rows:
        key = row["key"]
        row_id = row["row_id"]
        try:
            sid = find_id_by_key(cfg["project_id"], key, cfg["token"])
        except urllib.error.HTTPError as e:
            log_rows.append({**row, "status": f"HTTP_ERROR_LOOKUP({e.code})", "http_code": ""})
            continue
        time.sleep(0.3)
        if sid is None:
            log_rows.append({**row, "status": "KEY_NOT_FOUND", "http_code": ""})
            continue
        if dry_run:
            log_rows.append({**row, "status": "DRY_RUN", "http_code": ""})
        else:
            try:
                code = patch_one(cfg["project_id"], sid, row["new_translation"], cfg["token"])
                log_rows.append({**row, "status": "OK", "http_code": code})
                ok_keys[row_id] = row["new_translation"]  # 按 row_id 存，不是按 key
            except urllib.error.HTTPError as e:
                log_rows.append({**row, "status": "HTTP_ERROR", "http_code": e.code})
            time.sleep(0.4)

    for cr in conflict_rows:
        log_rows.append({**cr, "status": "CONFLICT_SAME_KEY_DIFFERENT_TRANSLATION", "http_code": ""})

    with open(PUSH_LOG_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["row_id", "key", "old_translation", "new_translation", "status", "http_code"])
        w.writeheader()
        w.writerows(log_rows)

    reset_count = 0
    # 重置阶段也要用 row_id 定位，不能再用 key（同 key 多行时会全部命中）
    if not dry_run and ok_keys:
        for r in rows:
            hit = ok_keys.get(r["row_id"])
            if hit:
                r["current_translation"] = hit
                r["decision"] = ""
                r["final_translation"] = ""
                r["reviewer_note"] = (r.get("reviewer_note") or "") + " [已推送]"
                reset_count += 1
        write_decisions(rows)

    not_found = sum(1 for r in log_rows if r["status"] == "KEY_NOT_FOUND")
    return jsonify({
        "ok": True, "dry_run": dry_run, "total": len(patch_rows),
        "not_found": not_found, "reset_count": reset_count, "log": log_rows,
        "skipped_excluded": skipped_excluded, "conflicts": len(conflicts),
    })


@app.get("/api/push-log")
def api_push_log():
    if not PUSH_LOG_PATH.exists():
        return jsonify({"rows": []})
    with open(PUSH_LOG_PATH, encoding="utf-8-sig", newline="") as f:
        return jsonify({"rows": list(csv.DictReader(f))})


# ----------------------------------------------------------- ai (stub) -----

@app.post("/api/ai-assist")
def api_ai_assist():
    # 预留接口：目前不接入 AI，直接返回未实现。
    # 未来可复用 4_ai_assist_fill.py 的 prompt 模板，在这里调用 anthropic SDK。
    return jsonify({"ok": False, "error": "AI 辅助尚未接入，接口已预留 (POST /api/ai-assist)"}), 501


# ------------------------------------------------------------------ pages --

@app.get("/")
def index():
    return send_from_directory("templates", "index.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)