import subprocess
import urllib.request
from datetime import datetime

try:
    url = "https://wttr.in/衡阳?format=%C+%t+%h+%w&lang=zh"
    raw = urllib.request.urlopen(url, timeout=10).read()
    weather = raw.decode("utf-8").strip()
except Exception as e:
    weather = f"fail:{e}"

now = datetime.now().strftime("%Y-%m-%d %H:%M")
line = f"- {now} \u5929\u6c14: {weather}"
# 用文件写，绕开编码
with open("C:\\Users\\liu\\AppData\\Local\\Temp\\weather.txt", "w", encoding="utf-8") as f:
    f.write(line)
subprocess.run(
    'obsidian vault="我的笔记" append file=日志 content<"C:\\Users\\liu\\AppData\\Local\\Temp\\weather.txt"', shell=True
)
