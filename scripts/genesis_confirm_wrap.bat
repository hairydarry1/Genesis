@echo off
rem Breadcrumb wrapper for scheduled task "Genesis FlipConfirm Daily".
rem Purpose: distinguish "task never started" from "killed mid-run" after the
rem Sep 27-28 wake-catchup kills (LastTaskResult 0xC000013A, zero log bytes).
cd /d C:\Users\hp\Desktop\code\Genesis
echo [%date% %time%] confirm start >> archive\genesis_task_breadcrumbs.log
rem NOTE: stdout goes to a SEPARATE sink - flip_confirm_longonly.py opens
rem flip_confirm_longonly.log itself with open(...,"a"), and a cmd `>>`
rem redirect on the same file denies that second open (same class of bug
rem fixed in the checkpoint wrapper Sep 28).
"C:\Users\hp\AppData\Local\Python\pythoncore-3.14-64\python.exe" analysis\flip_confirm_longonly.py >> archive\flip_confirm_stdout.log 2>&1
echo [%date% %time%] confirm end rc=%errorlevel% >> archive\genesis_task_breadcrumbs.log
