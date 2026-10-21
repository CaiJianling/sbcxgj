@echo off
chcp 936 1>NUL
cd /d "%~dp0."

if not exist ".venv\Scripts\python.exe" goto :no_venv
if not exist "devices.db" goto :no_db

set "PORT=%~1"
if "%PORT%"=="" (
  echo 未指定端口，使用 config.ini 里的设置。
  ".venv\Scripts\python.exe" app.py
) else (
  echo 正在按端口 %PORT% 启动设备查询网页 ...
  ".venv\Scripts\python.exe" app.py --port %PORT%
)
goto :end

:no_venv
echo [缺少虚拟环境] 请先在本目录的命令行里执行下面两行：
echo     python -m venv .venv
echo     .venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
goto :end

:no_db
echo [缺少数据库] 没找到 devices.db ，请先双击“更新数据.bat”导入 Excel。
goto :end

:end
pause
