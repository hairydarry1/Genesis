@echo off
rem Daily incremental Flip tick collection (scheduled task "Genesis FlipTick Daily")
cd /d C:\Users\hp\Desktop\code\Genesis
"C:\Users\hp\AppData\Local\Python\pythoncore-3.14-64\python.exe" data\flip_tick_store.py >> archive\flip_tick_store.log 2>&1
