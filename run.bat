@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Creating Python virtual environment...
  py -3.11 -m venv .venv
)
call .venv\Scripts\activate
python -m pip install --upgrade pip
rem Legacy Streamlit MVP (kept working). The new application is started with start.bat.
pip install -r requirements-legacy.txt
streamlit run app\dashboard.py
pause
