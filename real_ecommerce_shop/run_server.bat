@echo off
chcp 65001 > nul
title TechStore - Web Ban Hang E-Commerce
cd /d "%~dp0"
echo =========================================================
echo Đang khởi động Web Bán Hàng TechStore (Port 5000)...
echo =========================================================
python server.py
pause
