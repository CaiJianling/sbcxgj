#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
设备查询工具 —— 内网网页查询界面（Flask + SQLite，需输入查询密码）

用法：
    .venv\\Scripts\\python.exe gen_password.py            # 先设置查询密码（写进 config.ini）
    .venv\\Scripts\\python.exe app.py                     # 端口等读 config.ini
    .venv\\Scripts\\python.exe app.py --port 8080         # 命令行指定端口，优先于配置文件
    .venv\\Scripts\\python.exe app.py --host 127.0.0.1 --no-auth   # 本机调试时免密码

配置优先级：命令行参数 > 环境变量(PORT/DEVICE_DB) > config.ini > 程序默认值
密码只以 SHA-256 哈希存在 config.ini 的 [security] query_password_sha256 里。
"""

import argparse
import csv
import datetime as dt
import hmac
import io
import os
import re
import sqlite3
import sys
from urllib.parse import quote as urlquote

import auth
from flask import (Flask, Response, abort, jsonify, redirect, render_template, request,
                   send_from_directory)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "devices.db")
DEFAULT_PORT = 8000
PASSWORD_FIELD = "terminal_pwd"

# 数据库列 -> 界面显示名（顺序 = 表格/导出顺序；库里没有的列会自动忽略）
COLUMNS = [
    ("asset_no", "资产编号"),
    ("building", "楼号"),
    ("floor", "楼层"),
    ("work_type", "工作类型"),
    ("work_group", "工作组"),
    ("dept", "科室"),
    ("room_no", "诊室号"),
    ("location", "位置"),
    ("category", "设备分类"),
    ("device_name", "设备名称"),
    ("brand", "品牌"),
    ("model", "型号"),
    ("make_date", "出厂日期"),
    ("make_year", "出厂年份"),
    ("ip", "IP地址"),
    ("computer_name", "计算机名"),
    (PASSWORD_FIELD, "终端密码"),
    ("monitor", "显示器"),
    ("network", "网络状态"),
    ("inspector", "巡检人员"),
    ("change_time", "变更时间"),
    ("remark", "备注"),
    ("transfer_tag", "转移标签"),
    ("tag_dept", "标签科室"),
    ("status", "状态"),
]
LABELS = dict(COLUMNS)
SETTABLE_KEYS = set(key for key, _ in COLUMNS) | {"id"}

# 下拉筛选的字段（取值数量有限，适合做选择框）
FACET_FIELDS = ["building", "floor", "work_type", "category", "device_name", "network", "brand"]
# 关键字搜索覆盖的字段：不含终端密码
SEARCH_FIELDS = ["asset_no", "building", "floor", "work_type", "work_group", "dept", "room_no",
                 "location", "category", "device_name", "brand", "model", "ip", "computer_name",
                 "monitor", "network", "inspector", "remark", "transfer_tag", "tag_dept", "status"]
# 支持精确匹配（可用逗号分隔多选）的查询参数
EXACT_FIELDS = FACET_FIELDS + ["work_group", "dept", "status"]
# 表格默认显示的列（列太多时收起其余，前端可切换“全部列”）
LIST_KEYS = ["asset_no", "building", "floor", "dept", "work_group", "room_no", "location",
             "category", "device_name", "brand", "model", "make_year", "ip", "network"]
# 排序时按“自然顺序”处理的字段（1楼、2楼、…、10楼）
NATURAL_SORT_KEYS = {"building", "floor"}

app = Flask(__name__, static_folder=os.path.join(HERE, "static"), static_url_path="/static")
DB_PATH = DEFAULT_DB

# 运行期参数，在 main() 里按 “命令行 > 环境变量 > config.ini > 默认值” 覆盖
AUTH_ENABLED = True          # False 时不需要密码（本机调试用 --no-auth）
PASSWORD_SHA256 = ""         # 查询密码的 SHA-256，来自 config.ini
SESSION_HOURS = 8            # 登录状态保持小时数
THROTTLE = auth.Throttle(max_fails=5, lock_seconds=300)
PUBLIC_PATHS = ("/login", "/logout", "/health", "/favicon.ico")


def is_logged_in():
    return auth.verify_token(request.cookies.get(auth.COOKIE_NAME), PASSWORD_SHA256)


def client_key():
    return request.remote_addr or "unknown"


@app.before_request
def require_login():
    """除了登录页、静态资源和健康检查，其它都要先验证密码。"""
    if not AUTH_ENABLED or request.path in PUBLIC_PATHS or request.path.startswith("/static/"):
        return None
    if is_logged_in():
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "未登录或登录已过期", "loginUrl": "/login"}), 401
    return redirect("/login")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def quote(key):
    return '"%s"' % key


def table_keys():
    """devices 表里实际存在的列（排除内部 id）。"""
    conn = get_db()
    try:
        return [row[1] for row in conn.execute("PRAGMA table_info(devices)") if row[1] != "id"]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 查询条件构造
# --------------------------------------------------------------------------
def split_values(raw):
    """支持用逗号/顿号/空格/分号分隔的多值筛选。"""
    if raw is None:
        return []
    return [p for p in re.split(r"[,，、;；\s]+", str(raw).strip()) if p]


def like_pattern(value):
    """用户输入 -> LIKE 模糊匹配串，转义 \\ % _ 三个特殊字符。"""
    return "%" + str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def build_where(args):
    where, params = [], []

    def add_like(field, value):
        where.append("IFNULL(%s,'') LIKE ? ESCAPE '\\'" % quote(field))
        params.append(like_pattern(value))

    keyword = (args.get("keyword") or "").strip()
    if keyword:
        clauses = []
        for field in SEARCH_FIELDS:
            clauses.append("IFNULL(%s,'') LIKE ? ESCAPE '\\'" % quote(field))
            params.append(like_pattern(keyword))
        where.append("(%s)" % " OR ".join(clauses))

    asset = (args.get("asset") or "").strip()
    if asset:
        values = split_values(asset) or [asset]
        if args.get("exact") == "1" and len(values) == 1:
            where.append("%s = ?" % quote("asset_no"))
            params.append(values[0])
        elif len(values) > 1:
            where.append("%s IN (%s)" % (quote("asset_no"), ",".join("?" * len(values))))
            params.extend(values)
        else:
            add_like("asset_no", asset)

    for field in EXACT_FIELDS:
        values = split_values(args.get(field))
        if values:
            where.append("%s IN (%s)" % (quote(field), ",".join("?" * len(values))))
            params.extend(values)

    for field, arg_name in (("model", "model"), ("ip", "ip"), ("computer_name", "computer_name"),
                            ("dept", "dept_like"), ("work_group", "work_group_like"),
                            ("location", "location_like"), ("remark", "remark_like"),
                            ("room_no", "room_no_like")):
        value = (args.get(arg_name) or "").strip()
        if value:
            add_like(field, value)

    year_from = to_year(args.get("year_from"))
    year_to = to_year(args.get("year_to"))
    if year_from is not None:
        where.append("make_year GLOB '[0-9]*' AND CAST(make_year AS INTEGER) >= ?")
        params.append(year_from)
    if year_to is not None:
        where.append("make_year GLOB '[0-9]*' AND CAST(make_year AS INTEGER) <= ?")
        params.append(year_to)
    if args.get("no_year") == "1":
        where.append("(make_year IS NULL OR make_year NOT GLOB '[0-9]*')")

    return ("WHERE " + " AND ".join(where)) if where else "", params


def to_year(value):
    value = (value or "").strip()
    return int(value) if value.isdigit() else None


def build_order(args, keys):
    sort = args.get("sort")
    if sort not in SETTABLE_KEYS or (sort != "id" and sort not in keys):
        sort = "id"
    order = "DESC" if (args.get("order") or "").lower().startswith("desc") else "ASC"
    column = quote(sort)
    if sort in NATURAL_SORT_KEYS:
        # “1楼 / 10楼 / 19楼”这类带数字的文本，先按长度再按字典序，接近数字大小顺序
        return "ORDER BY LENGTH(%s) %s, %s COLLATE NOCASE %s" % (column, order, column, order)
    if sort == "id":
        return "ORDER BY id %s" % order
    return "ORDER BY %s COLLATE NOCASE %s, id ASC" % (column, order)


def mask_row(row, show_password):
    item = dict(row)
    if not show_password and item.get(PASSWORD_FIELD):
        item[PASSWORD_FIELD] = "****"
    return item


# --------------------------------------------------------------------------
# 路由
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    """登录页 + 校验查询密码；成功后签发会话 Cookie。"""
    if not AUTH_ENABLED or is_logged_in():
        return redirect("/")

    error, seconds_left = "", 0
    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form
        given = str(payload.get("password") or "")
        seconds_left = THROTTLE.blocked_seconds(client_key())
        if seconds_left:
            error = "输错次数过多，请 %d 秒后再试。" % seconds_left
        elif hmac.compare_digest(auth.hash_password(given), PASSWORD_SHA256):
            THROTTLE.clear(client_key())
            response = jsonify({"ok": True, "redirect": "/"}) if _wants_json() else redirect("/")
            token = auth.make_token(PASSWORD_SHA256, SESSION_HOURS)
            max_age = SESSION_HOURS * 3600 if SESSION_HOURS > 0 else None
            response.set_cookie(auth.COOKIE_NAME, token, max_age=max_age, path="/",
                                httponly=True, samesite="Lax")
            return response
        else:
            locked_minutes = THROTTLE.fail(client_key())
            if locked_minutes:
                error = "密码不正确，已连续输错，已临时锁定 %d 分钟。" % locked_minutes
            else:
                error = ("密码不正确，还可尝试 %d 次。"
                         % THROTTLE.remaining_attempts(client_key()))

    if request.method == "POST" and _wants_json() and error:
        return jsonify({"ok": False, "error": error}), 401
    return render_template("login.html", error=error, session_hours=SESSION_HOURS,
                           max_fails=THROTTLE.max_fails,
                           lock_minutes=max(1, THROTTLE.lock_seconds // 60))


@app.route("/logout", methods=["GET", "POST"])
def logout():
    response = redirect("/login")
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


def _wants_json():
    """登录请求是表单还是 fetch(JSON)，两种都支持。"""
    if request.is_json:
        return True
    accept = request.headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept


@app.route("/health")
def health():
    return jsonify({"ok": True, "auth": AUTH_ENABLED})


@app.route("/api/config")
def api_config():
    """前端初始化：字段清单 + 下拉选项 + 数据源信息。"""
    conn = get_db()
    try:
        rows = conn.execute("PRAGMA table_info(devices)").fetchall()
        keys = [r[1] for r in rows if r[1] != "id"]
        facets = {}
        for field in FACET_FIELDS + ["work_group", "dept"]:
            if field not in keys:
                continue
            facets[field] = [{"value": r["v"], "count": r["n"]} for r in conn.execute(
                "SELECT %s AS v, COUNT(1) AS n FROM devices "
                "GROUP BY %s HAVING TRIM(IFNULL(v, '')) <> '' ORDER BY n DESC, v COLLATE NOCASE ASC"
                % (quote(field), quote(field)))]
        years = [r["v"] for r in conn.execute(
            "SELECT make_year AS v FROM devices WHERE make_year GLOB '[0-9]*' "
            "GROUP BY make_year ORDER BY CAST(make_year AS INTEGER) ASC")]
        meta = {}
        if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
            meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
        total = conn.execute("SELECT COUNT(1) FROM devices").fetchone()[0]
    finally:
        conn.close()

    return jsonify({
        "columns": [{"key": k, "label": LABELS.get(k, k)} for k in keys],
        "listKeys": [k for k in LIST_KEYS if k in keys],
        "facets": facets,
        "years": years,
        "total": total,
        "meta": meta,
        "passwordField": PASSWORD_FIELD,
    })


@app.route("/api/devices")
def api_devices():
    keys = table_keys()
    where, params = build_where(request.args)
    page_size = to_int(request.args.get("page_size"), 50, 1, 1000)
    page = to_int(request.args.get("page"), 1, 1, 100000)
    show_pwd = request.args.get("show_pwd") == "1"

    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(1) FROM devices %s" % where, params).fetchone()[0]
        rows = conn.execute("SELECT %s, id FROM devices %s %s LIMIT ? OFFSET ?" % (
            ", ".join(quote(k) for k in keys), where, build_order(request.args, keys)),
            params + [page_size, (page - 1) * page_size]).fetchall()
        stats = {}
        for field in ("building", "category", "network"):
            if field in keys:
                stats[field] = [{"value": r["v"], "count": r["n"]} for r in conn.execute(
                    "SELECT %s AS v, COUNT(1) AS n FROM devices %s "
                    "GROUP BY %s HAVING TRIM(IFNULL(v, '')) <> '' ORDER BY n DESC LIMIT 10"
                    % (quote(field), where, quote(field)), params)]
        dupes = 0
        if "asset_no" in keys:
            dupes = conn.execute(
                "SELECT COUNT(1) FROM (SELECT asset_no FROM devices %s%s "
                "GROUP BY asset_no HAVING COUNT(1) > 1)"
                % (where + " AND" if where else "WHERE", " asset_no IS NOT NULL"), params).fetchone()[0]
    finally:
        conn.close()

    return jsonify({
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
        "items": [mask_row(r, show_pwd) for r in rows],
        "stats": stats,
        "duplicateAssetNos": dupes,
    })


def to_int(raw, default, minimum, maximum):
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


@app.route("/api/export.csv")
def api_export():
    keys = table_keys()
    where, params = build_where(request.args)
    show_pwd = request.args.get("show_pwd") == "1"
    conn = get_db()
    try:
        rows = conn.execute("SELECT %s FROM devices %s %s" % (
            ", ".join(quote(k) for k in keys), where, build_order(request.args, keys)), params).fetchall()
    finally:
        conn.close()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([LABELS.get(k, k) for k in keys])
    for row in rows:
        item = mask_row(row, show_pwd)
        writer.writerow(["" if item.get(k) is None else item.get(k) for k in keys])

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
    filename = "设备查询_%s.csv" % stamp
    body = "﻿" + buffer.getvalue()  # BOM，方便 Excel 直接打开
    return Response(
        body.encode("utf-8"),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 "attachment; filename=devices_%s.csv; filename*=UTF-8''%s" % (stamp, urlquote(filename))},
    )


@app.route("/api/detail/<int:row_id>")
def api_detail(row_id):
    keys = table_keys()
    conn = get_db()
    try:
        row = conn.execute("SELECT id, %s FROM devices WHERE id = ?" % ", ".join(quote(k) for k in keys),
                           (row_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        abort(404)
    show_pwd = request.args.get("show_pwd") == "1"
    item = mask_row(row, show_pwd)
    return jsonify({
        "id": row["id"],
        "fields": [{"key": k, "label": LABELS.get(k, k), "value": item.get(k) or ""} for k in keys],
    })


# --------------------------------------------------------------------------
# 启动
# --------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description="设备查询网页服务")
    parser.add_argument("--port", type=int, help="网页端口；不填则用环境变量 PORT 或 config.ini 的 port")
    parser.add_argument("--host", help="监听地址；不填则用 config.ini 的 host（默认 0.0.0.0，本机+同事都能访问）")
    parser.add_argument("--db", help="SQLite 数据库路径；不填则用 config.ini 的 db（默认项目下 devices.db）")
    parser.add_argument("--config", help="配置文件路径，默认项目目录下 config.ini")
    parser.add_argument("--no-auth", action="store_true",
                        help="本机调试时不要求输入查询密码（正式环境别用）")
    parser.add_argument("--debug", action="store_true", help="开发模式")
    args = parser.parse_args(argv)

    cfg = auth.load_config(args.config)
    env_port = os.environ.get("PORT", "").strip()
    port = args.port or (int(env_port) if env_port.isdigit() else cfg["port"])
    host = args.host or cfg["host"]
    db_path = args.db or os.environ.get("DEVICE_DB") or cfg["db"]

    global DB_PATH, AUTH_ENABLED, PASSWORD_SHA256, SESSION_HOURS
    DB_PATH = os.path.abspath(db_path)
    SESSION_HOURS = cfg["session_hours"]
    AUTH_ENABLED = not (args.no_auth or cfg["allow_no_password"])

    if AUTH_ENABLED and not auth.looks_like_hash(cfg["query_password_sha256"]):
        print("[错误] 还没有设置查询密码，服务不启动（避免内网任何人都能查）。")
        print("       设置密码：  .venv\\Scripts\\python.exe gen_password.py")
        print("       配置文件：  %s" % cfg["path"])
        print("       本机调试不想输密码：  .venv\\Scripts\\python.exe app.py --no-auth")
        return 1
    if not os.path.isfile(DB_PATH):
        print("[错误] 没有找到数据库：%s" % DB_PATH)
        print("       请先导入 Excel：  .venv\\Scripts\\python.exe import_data.py")
        return 1

    PASSWORD_SHA256 = cfg["query_password_sha256"].lower()
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(1) FROM devices").fetchone()[0]
    finally:
        conn.close()

    print("=" * 62)
    print(" 设备查询工具已启动")
    print("   数据库： %s（%d 条记录）" % (DB_PATH, total))
    print("   配置文件： %s%s" % (cfg["path"], "" if cfg["exists"] else "（不存在，全部用默认值）"))
    print("   本机访问： http://127.0.0.1:%d" % port)
    print("   同事访问： http://<本机IP>:%d  （ipconfig 可查本机 IP）" % port)
    if AUTH_ENABLED:
        print("   查询密码： 已启用，密码哈希来自配置文件；登录有效 %s"
              % ("%d 小时" % SESSION_HOURS if SESSION_HOURS > 0 else "到关闭浏览器"))
    else:
        print("   查询密码： 未启用（--no-auth 或 allow_no_password=true），任何能访问该端口的人都能查！")
    print("   停止服务： Ctrl+C")
    print("=" * 62)
    app.run(host=host, port=port, debug=args.debug, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    sys.exit(main())
