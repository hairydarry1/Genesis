@echo off
rem Breadcrumb wrapper for scheduled task "Genesis FlipCheckpoint Daily".
rem Purpose: distinguish "task never started" from "killed mid-run" after the
rem Sep 27-28 wake-catchup kills (LastTaskResult 0xC000013A, zero log bytes).
cd /d C:\Users\hp\Desktop\code\Genesis
echo [%date% %time%] checkpoint start >> archive\genesis_task_breadcrumbs.log
rem NOTE: stdout goes to a SEPARATE sink - flip_checkpoint.py opens
rem flip_checkpoint.log itself with open(...,"a"), and a cmd `>>` redirect
rem on the same file denies that second open (PermissionError, seen Sep 28).
"C:\Users\hp\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\flip_checkpoint.py >> archive\flip_checkpoint_stdout.log 2>&1
echo [%date% %time%] checkpoint end rc=%errorlevel% >> archive\genesis_task_breadcrumbs.log
