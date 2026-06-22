import subprocess
import urllib.request
from datetime import datetime

city = "衡阳"
try:
    url = f"https://wttr.in/{city}?format=%C+%t+%h+%w"
    req = urllib.request.urlopen(url, timeout=10)
    weather = req.read().decode("utf-8").strip()
except Exception as e:
    weather = f"error: {e}"

line = f"- {datetime.now().strftime('%Y-%m-%d %H:%M')} weather: {weather}"
# PowerShell管道处理utf-8
ps_cmd = f'$env:PYTHONIOENCODING="utf-8"; Write-Output "{line}" | obsidian vault="我的笔记" append file=日志'
subprocess.run(["powershell", "-Command", ps_cmd], shell=True)
