@echo off
chcp 65001 > nul
title Tool Tan Cong DoS / DDoS Layer 7
cd /d "%~dp0"
python attacker.py
pause
