#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把《信息终端资产表》Excel 的第一个明细标签页导入 SQLite。

用法（在项目目录下，使用 .venv 虚拟环境）：
    .venv\\Scripts\\python.exe import_data.py                       # 自动找当前目录的 xlsx
    .venv\\Scripts\\python.exe import_data.py --excel "xxx.xlsx" --db devices.db
    .venv\\Scripts\\python.exe import_data.py --rebuild             # 先删表再导入

说明：
    表头位于第 4 行，数据从第 5 行开始；只导入明细主表（默认 sheet：1电脑打印机明细）。
"""

import argparse
import datetime as dt
import os
import re
import sqlite3
import sys
import unicodedata

import openpyxl

# 默认工作表名（第一个标签页）
DEFAULT_SHEET = "1电脑打印机明细"
DEFAULT_HEADER_ROW = 4
DEFAULT_DB = "devices.db"

# Excel 表头 -> (数据库列名, 界面显示名)。顺序即导出/展示顺序。
COLUMN_MAP = [
    ("资产编号", "asset_no", "资产编号"),
    ("楼号", "building", "楼号"),
    ("楼层", "floor", "楼层"),
    ("工作类型", "work_type", "工作类型"),
    ("工作组", "work_group", "工作组"),
    ("科室", "dept", "科室"),
    ("诊室号", "room_no", "诊室号"),
    ("位置", "location", "位置"),
    ("设备分类", "category", "设备分类"),
    ("设备名称", "device_name", "设备名称"),
    ("品牌", "brand", "品牌"),
    ("型号", "model", "型号"),
    ("出厂日期", "make_date", "出厂日期"),
    ("出厂年份", "make_year", "出厂年份"),
    ("IP地址", "ip", "IP地址"),
    ("计算机名", "computer_name", "计算机名"),
    ("终端密码", "terminal_pwd", "终端密码"),
    ("显示器", "monitor", "显示器"),
    ("网络状态", "network", "网络状态"),
    ("巡检人员", "inspector", "巡检人员"),
    ("变更时间", "change_time", "变更时间"),
    ("备注", "remark", "备注"),
    ("转移标签", "transfer_tag", "转移标签"),
    ("标签科室", "tag_dept", "标签科室"),
    ("状态", "status", "状态"),
]
# 允许在 Excel 表头里带换行/空格，归一化后再匹配
NORMALIZE_RE = re.compile(r"\s+")


def normalize_header(value):
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    return NORMALIZE_RE.sub("", text)


def clean_cell(value):
    """把单元格统一成可入库的文本；全空返回 None。"""
    if value is None:
        return None
    if isinstance(value, (dt.datetime, dt.date)):
        # 日期型单元格：年月日时分秒都为 0 时只保留 年-月
        if value.hour == 0 and value.minute == 0 and value.second == 0:
            return value.strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return repr(value)
    text = NORMALIZE_RE.sub(" ", str(value)).strip()
    return text or None


def find_excel(explicit_path, folder):
    """定位要导入的 Excel 文件。"""
    if explicit_path:
        if not os.path.isfile(explicit_path):
            sys.exit("[错误] 找不到 Excel 文件：%s" % explicit_path)
        return os.path.abspath(explicit_path)
    candidates = [
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.lower().endswith(".xlsx") and not name.startswith("~$")
    ]
    if not candidates:
        sys.exit("[错误] 当前目录没有 .xlsx 文件，请用 --excel 指定：folder=%s" % folder)
    # 多个时取修改时间最新的一个
    candidates.sort(key=os.path.getmtime, reverse=True)
    if len(candidates) > 1:
        names = "、".join(os.path.basename(c) for c in candidates)
        print("[提示] 目录下有多个 xlsx，使用最新的：%s（其余：%s）"
              % (os.path.basename(candidates[0]), names))
    return os.path.abspath(candidates[0])


def read_sheet(path, sheet_name, header_row):
    """读取指定工作表，返回 (显示列名, 数据库列名, 数据行, 实际标签页名)。"""
    print("[1/3] 打开 Excel：%s" % path)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    names = wb.sheetnames
    ws = wb[sheet_name] if sheet_name in names else None
    if ws is None:
        # 名称不完全一致时，退化为第一个标签页
        print("[提示] 没有名为“%s”的标签页，改用第一个标签页“%s”" % (sheet_name, names[0]))
        ws = wb[names[0]]
    print("      标签页：%s（共 %d 行）" % (ws.title, ws.max_row))

    rows = ws.iter_rows(min_row=1, values_only=True)
    header = None
    for index, row in enumerate(rows, start=1):
        if index == header_row:
            header = list(row)
            break
    if header is None:
        sys.exit("[错误] 表头行 %d 不存在" % header_row)

    known = {normalize_header(a): (b, c) for a, b, c in COLUMN_MAP}
    db_cols, display_cols = [], []
    header_indexes = []
    for position, raw in enumerate(header):
        key = normalize_header(raw)
        if not key:
            continue
        if key in known:
            db_name, display = known[key]
        else:
            # 表头新增的未知列，也保留下来，避免信息丢失
            db_name = "extra_%d" % (position + 1)
            display = "%s（新增列）" % key
            print("      [注意] 未识别的列“%s”，已存为 %s" % (key, db_name))
        if db_name in db_cols:
            continue
        db_cols.append(db_name)
        display_cols.append(display)
        header_indexes.append(position)

    data = []
    skipped = 0
    for row in rows:
        values = [clean_cell(row[i]) if i < len(row) else None for i in header_indexes]
        if not any(values):
            skipped += 1
            continue
        data.append(values)
    wb.close()
    print("[2/3] 读取到 %d 条记录（跳过空行 %d 条，列 %d 个）"
          % (len(data), skipped, len(db_cols)))
    return display_cols, db_cols, data, ws.title


def write_db(db_path, db_cols, data, source, sheet_title, rebuild):
    print("[3/3] 写入 SQLite：%s" % db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    if rebuild:
        cur.execute("DROP TABLE IF EXISTS devices")
        cur.execute("DROP TABLE IF EXISTS meta")

    exists = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='devices'").fetchone()
    if exists:
        old = {r[1] for r in cur.execute("PRAGMA table_info(devices)")}
        if old != {"id"} | set(db_cols):
            print("      [提示] 已有表结构与本次导入不同，将重建 devices 表")
            cur.execute("DROP TABLE devices")
            exists = None
    if not exists:
        columns_sql = ",\n    ".join('"%s" TEXT' % c for c in db_cols)
        cur.execute('CREATE TABLE devices (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    %s\n)'
                    % columns_sql)
        cur.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    else:
        cur.execute("DELETE FROM devices")

    placeholders = ",".join("?" * len(db_cols))
    quoted = ",".join('"%s"' % c for c in db_cols)
    cur.executemany("INSERT INTO devices (%s) VALUES (%s)" % (quoted, placeholders), data)

    indexed = ["building", "floor", "dept", "category", "network", "asset_no"]
    for column in indexed:
        if column in db_cols:
            cur.execute('CREATE INDEX IF NOT EXISTS "idx_{0}" ON devices ("{0}")'.format(column))

    cur.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('source_file', ?)", (source,))
    cur.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('sheet_name', ?)", (sheet_title,))
    cur.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('imported_at', ?)",
                (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
    cur.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('row_count', ?)",
                (str(cur.execute("SELECT COUNT(1) FROM devices").fetchone()[0]),))
    conn.commit()

    total = cur.execute("SELECT COUNT(1) FROM devices").fetchone()[0]
    dupes = cur.execute(
        "SELECT COUNT(1) FROM (SELECT asset_no FROM devices WHERE asset_no IS NOT NULL "
        "GROUP BY asset_no HAVING COUNT(1) > 1)"
    ).fetchone()[0] if "asset_no" in db_cols else 0
    conn.close()
    print("      完成：devices 表现有 %d 条记录；重复资产编号 %d 个（同一编号可能对应多台设备）"
          % (total, dupes))


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="导入资产 Excel 到 SQLite")
    parser.add_argument("--excel", help="Excel 文件路径；不填则自动取当前目录最新的 xlsx")
    parser.add_argument("--sheet", default=DEFAULT_SHEET, help="标签页名称，默认 %s" % DEFAULT_SHEET)
    parser.add_argument("--header-row", type=int, default=DEFAULT_HEADER_ROW,
                        help="表头所在行，默认第 %d 行" % DEFAULT_HEADER_ROW)
    parser.add_argument("--db", default=os.path.join(here, DEFAULT_DB), help="SQLite 数据库路径")
    parser.add_argument("--rebuild", action="store_true", help="先删除旧表再导入")
    args = parser.parse_args(argv)

    path = find_excel(args.excel, here)
    display_cols, db_cols, data, sheet_title = read_sheet(path, args.sheet, args.header_row)
    if not data:
        sys.exit("[错误] 没有读到任何数据，请检查 --sheet / --header-row")
    print("      导入字段：%s" % "、".join(display_cols))
    write_db(os.path.abspath(args.db), db_cols, data, os.path.basename(path), sheet_title,
             args.rebuild)
    print("\n下一步启动网页： .venv\\Scripts\\python.exe app.py --port 8000")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    sys.exit(main())
