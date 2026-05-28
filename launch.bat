@echo off
cd /d D:\Coding_yespinm\news-digest
echo 啟動新聞摘要工具...
echo.
call .venv\Scripts\activate
.venv\Scripts\python news_fetcher.py
pause
