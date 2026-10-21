@echo off
chcp 936 1>NUL
cd /d "%~dp0."

if not exist ".venv\Scripts\python.exe" goto :no_venv

echo 正在把本目录最新的 Excel 明细表导入数据库 devices.db ...
".venv\Scripts\python.exe" import_data.py %*
echo 导入完成后，双击“启动查询.bat”即可查看。
goto :end

:no_venv
echo [缺少虚拟环境] 请先在本目录的命令行里执行下面两行：
echo     python -m venv .venv
echo     .venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
goto :end

:end
pause
