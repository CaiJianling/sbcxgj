#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
配置与登录校验（只存 SHA-256 哈希，配置文件里看不到明文密码）。

config.ini 示例：
    [server]
    host = 0.0.0.0
    port = 8000
    session_hours = 8
    db = devices.db

    [security]
    # 查询密码的 SHA-256 哈希；改密码请运行 gen_password.py，不要手填
    query_password_sha256 = 6b3f...
    allow_no_password = false

token = “过期时间戳.HMAC-SHA256(哈希, 过期时间戳)”，改密码后旧登录状态自动失效。
"""

import configparser
import hashlib
import hmac
import os
import re
import threading
import time

CONFIG_NAME = "config.ini"
COOKIE_NAME = "device_query_session"

DEFAULTS = {
    "host": "0.0.0.0",
    "port": 8000,
    "session_hours": 8,
    "db": "devices.db",
    "query_password_sha256": "",
    "allow_no_password": False,
}

HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")

# 配置项 -> 所在小节
KEY_SECTION = {
    "host": "server",
    "port": "server",
    "session_hours": "server",
    "db": "server",
    "query_password_sha256": "security",
    "allow_no_password": "security",
}


# --------------------------------------------------------------------------
# 配置文件读写
# --------------------------------------------------------------------------
def hash_password(password):
    """查询密码 -> SHA-256 十六进制（与 echo -n xxx | sha256sum 结果一致）。"""
    return hashlib.sha256(str(password).encode("utf-8")).hexdigest()


def looks_like_hash(value):
    return bool(value) and bool(HEX64.match(str(value).strip()))


def _as_int(raw, default, minimum, maximum):
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def load_config(path=None):
    """读取 config.ini；缺失的项用默认值补齐。文件不存在也能正常返回默认配置。"""
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_NAME)
    path = os.path.abspath(path)
    parser = configparser.ConfigParser()
    exists = os.path.isfile(path)
    if exists:
        with open(path, "rb") as handle:
            raw = handle.read()
        # 用记事本改过、存成 ANSI(GBK) 也能读
        for encoding in ("utf-8-sig", "gbk"):
            try:
                parser.read_string(raw.decode(encoding))
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("config.ini 编码无法识别（请用 UTF-8 或 GBK 保存）：%s" % path)

    def pick(section, key, fallback):
        if parser.has_option(section, key):
            return str(parser.get(section, key)).strip()
        return str(fallback)

    db = pick("server", "db", DEFAULTS["db"])
    if db and not os.path.isabs(db):
        db = os.path.join(os.path.dirname(path), db)

    return {
        "path": path,
        "exists": exists,
        "host": pick("server", "host", DEFAULTS["host"]),
        "port": _as_int(pick("server", "port", DEFAULTS["port"]), DEFAULTS["port"], 1, 65535),
        "session_hours": _as_int(pick("server", "session_hours", DEFAULTS["session_hours"]),
                                 DEFAULTS["session_hours"], 0, 24 * 30),
        "db": os.path.abspath(db),
        "query_password_sha256": pick("security", "query_password_sha256", "").lower(),
        "allow_no_password": pick("security", "allow_no_password", "false").lower()
                             in ("1", "true", "yes", "on"),
    }


TEMPLATE = """# 设备查询工具配置文件（改完重启 app.py 或双击“启动查询.bat”生效）
#   端口：把 port 改成想要的数字，或临时用  启动查询.bat 8080
#   host：0.0.0.0 = 本机同事都能访问；127.0.0.1 = 只允许本机
#   session_hours：登录后免输密码的小时数，0 = 关闭浏览器就要重新登录
#   密码：别手填，运行  gen_password.py  （或双击“设置密码.bat”），这里只保存 SHA-256 哈希

[server]
host = {host}
port = {port}
session_hours = {session_hours}
db = {db}

[security]
# true = 不要求密码，仅本机调试用
allow_no_password = {allow_no_password}
query_password_sha256 = {query_password_sha256}
"""


def write_config(updates, path=None):
    """合并现有配置后按模板重写 config.ini；只接受已知的配置项。"""
    for key in updates:
        if key not in KEY_SECTION:
            raise KeyError("未知配置项：%s" % key)

    current = load_config(path)
    db_value = updates.get("db", current["db"])
    if os.path.isabs(db_value):
        try:
            relative = os.path.relpath(db_value, os.path.dirname(current["path"]))
            if not relative.startswith(".."):
                db_value = relative
        except ValueError:  # 跨盘符时保留绝对路径
            pass

    flag = updates.get("allow_no_password", current["allow_no_password"])
    merged = {
        "host": updates.get("host", current["host"]),
        "port": updates.get("port", current["port"]),
        "session_hours": updates.get("session_hours", current["session_hours"]),
        "db": db_value,
        "allow_no_password": "true" if flag in (True, "true", "1", "yes", "on") else "false",
        "query_password_sha256": updates.get("query_password_sha256", current["query_password_sha256"]),
    }
    text = TEMPLATE.format(**merged).replace("\n", "\r\n")
    with open(current["path"], "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return current["path"]


# --------------------------------------------------------------------------
# 登录状态（Cookie）
# --------------------------------------------------------------------------
def _signature(secret, expiry):
    return hmac.new(secret.encode("utf-8"), str(expiry).encode("ascii"), hashlib.sha256).hexdigest()


def make_token(password_sha256, session_hours):
    """生成带过期时间的会话值；secret 用密码哈希，改密码即让旧 Cookie 失效。

    session_hours<=0 时按 1 小时有效期签发（配合浏览器会话 Cookie，关掉浏览器即失效）。
    """
    expiry = int(time.time()) + max(1, int(session_hours)) * 3600
    return "%d.%s" % (expiry, _signature(password_sha256, expiry))


def verify_token(token, password_sha256):
    if not token or not password_sha256:
        return False
    try:
        expiry_text, signature = str(token).split(".", 1)
        expiry = int(expiry_text)
    except ValueError:
        return False
    if expiry < int(time.time()):
        return False
    return hmac.compare_digest(signature, _signature(password_sha256, expiry))


# --------------------------------------------------------------------------
# 连续输错密码的简单限流
# --------------------------------------------------------------------------
class Throttle(object):
    """同一个来源 IP 连续输错 max_fails 次，锁定 lock_seconds 秒（内存计数，重启即清零）。"""

    def __init__(self, max_fails=5, lock_seconds=300):
        self.max_fails = max_fails
        self.lock_seconds = lock_seconds
        self._hits = {}
        self._lock = threading.Lock()

    def blocked_seconds(self, key):
        """>0 表示还在锁定期内，返回剩余秒数。"""
        with self._lock:
            _, until = self._hits.get(key, (0, 0))
            return max(0, int(until - time.time()))

    def fail(self, key):
        """记一次失败；若刚好触发锁定，返回锁定分钟数，否则返回 0。"""
        with self._lock:
            fails, until = self._hits.get(key, (0, 0))
            fails += 1
            if fails >= self.max_fails:
                self._hits[key] = (0, int(time.time()) + self.lock_seconds)
                return max(1, self.lock_seconds // 60)
            self._hits[key] = (fails, until)
            return 0

    def clear(self, key):
        with self._lock:
            self._hits.pop(key, None)

    def remaining_attempts(self, key):
        with self._lock:
            fails, _ = self._hits.get(key, (0, 0))
        return max(0, self.max_fails - fails)
