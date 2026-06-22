@echo off
setlocal enabledelayedexpansion
for /f "tokens=*" %%a in ('curl -s "https://wttr.in/衡阳?format=%%C+%%t+%%h+%%w"') do set weather=%%a
for /f "tokens=*" %%t in ('powershell -Command "Get-Date -Format 'yyyy-MM-dd HH:mm'"') do set now=%%t
obsidian vault="我的笔记" append file=日志 content="- !now! weather: !weather!"
