#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
设置/查看查询密码（config.ini 里只保存 SHA-256 哈希，不保存明文）。

用法（项目目录下）：
    .venv\\Scripts\\python.exe gen_password.py                 # 交互输入新密码（推荐，明文不进历史记录）
    .venv\\Scripts\\python.exe gen_password.py --set 你的密码   # 直接指定（会留在命令行历史，慎用）
    .venv\\Scripts\\python.exe gen_password.py --check         # 查看当前配置
    .venv\\Scripts\\python.exe gen_password.py --auth off      # 本机调试时临时免密码
    .venv\\Scripts\\python.exe gen_password.py --auth on       # 恢复必须输入密码
改完密码后，重启 app.py 生效；之前已登录的浏览器需要重新输入。
"""

import argparse
import getpass
import os
import sys

import auth

WEAK_LENGTH = 6


def read_secret(prompt, hide=True):
    """控制台上用免回显输入；管道/重定向（自动化脚本）时退化成读一行。"""
    try:
        interactive = sys.stdin.isatty()
    except (ValueError, OSError):
        interactive = False
    if hide and interactive:
        try:
            return getpass.getpass(prompt)
        except EOFError:
            sys.exit("[取消] 没有读到输入（输入窗口被关闭？）。")
    print(prompt.strip() + "：")
    line = sys.stdin.readline()
    if not line:
        sys.exit("[取消] 没有读到输入。")
    return line.strip()


def ask_password(confirm=True):
    first = read_secret("请输入查询密码（输入不显示）")
    if not first:
        sys.exit("[取消] 密码不能为空。")
    if confirm:
        second = read_secret("请再输入一次确认")
        if first != second:
            sys.exit("[取消] 两次输入不一致。")
    return first


def main(argv=None):
    parser = argparse.ArgumentParser(description="设置设备查询工具的查询密码")
    parser.add_argument("--set", dest="new_password", metavar="密码",
                        help="直接指定新密码（明文会出现在命令行历史里，建议改用交互输入）")
    parser.add_argument("--check", action="store_true", help="查看当前 config.ini 配置")
    parser.add_argument("--auth", choices=["on", "off"], help="on=必须输密码，off=本机调试免密码")
    parser.add_argument("--config", help="配置文件路径，默认项目目录下 config.ini")
    args = parser.parse_args(argv)

    cfg = auth.load_config(args.config)
    actions = []

    if args.check:
        print("配置文件： %s%s" % (cfg["path"], "" if cfg["exists"] else "（还不存在，运行本脚本会自动创建）"))
        print("监听地址： %s:%s" % (cfg["host"], cfg["port"]))
        print("数据库：   %s%s" % (cfg["db"], "" if os.path.isfile(cfg["db"]) else "（文件不存在，先跑 import_data.py）"))
        print("登录有效期：%d 小时" % cfg["session_hours"])
        if cfg["allow_no_password"]:
            print("查询密码： 已关闭（allow_no_password=true，任何能访问端口的人都能查）")
        elif auth.looks_like_hash(cfg["query_password_sha256"]):
            print("查询密码： 已设置，哈希前 12 位 = %s…" % cfg["query_password_sha256"][:12])
        else:
            print("查询密码： 未设置，服务会拒绝启动（运行 .venv\\Scripts\\python.exe gen_password.py 设置）")
        return 0

    if args.auth:
        actions.append({"allow_no_password": args.auth == "off"})
        print("[完成] 免密码调试模式 = %s" % ("开" if args.auth == "off" else "关"))

    if args.new_password is not None:
        password = args.new_password
        if not password.strip():
            sys.exit("[取消] --set 传入的密码为空。")
        actions.append({"query_password_sha256": auth.hash_password(password)})
        print("[提醒] 用 --set 传的明文会留在命令行历史里，正式环境建议不带参数重新运行一次。")
    elif not args.auth:
        password = ask_password()
        if len(password) < WEAK_LENGTH:
            print("[提醒] 密码短于 %d 位，建议加长一点。" % WEAK_LENGTH)
        actions.append({"query_password_sha256": auth.hash_password(password)})

    for updates in actions:
        path = auth.write_config(updates, args.config)
        for key, value in updates.items():
            if key == "query_password_sha256":
                print("[完成] 新密码哈希已写入 %s" % os.path.basename(path))
                print("       SHA-256 = %s" % value)
    if not args.auth and args.new_password is None:
        print("[完成] 请用这个新密码登录网页；重启 app.py 后生效，旧登录状态自动失效。")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    sys.exit(main())
