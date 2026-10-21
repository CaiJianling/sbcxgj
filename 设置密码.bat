@echo off
chcp 936 1>NUL
cd /d "%~dp0."

if not exist ".venv\Scripts\python.exe" goto :no_venv

echo 设置查询密码：按提示输入两次新密码（屏幕上不会显示），完成后写入 config.ini。
echo 配置文件里只保存 SHA-256 哈希，看不到明文；改密码后已登录的浏览器要重新输入。
echo.
".venv\Scripts\python.exe" gen_password.py %*
echo.
echo 想查看当前配置，可再运行一次并加参数：  设置密码.bat --check
goto :end

:no_venv
echo [缺少虚拟环境] 请先在本目录的命令行里执行下面两行：
echo     python -m venv .venv
echo     .venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
goto :end

:end
pause
