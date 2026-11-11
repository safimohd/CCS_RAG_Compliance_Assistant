"""RERA deployment entry point.

Streamlit Community Cloud identifies an app by repo + branch + main file path.
Two apps need two entry files: this one for RERA, app_healthcare.py for Healthcare.

The domain is set here via os.environ so it cannot be misconfigured per deployment.

    Main file path on Community Cloud:  05_App/app_rera.py

Locally:
    streamlit run 05_App/app_rera.py

Codebase separation:
    RERA:       app_rera.py -> user_flow_rera.py  (self-contained)
    Healthcare: app_healthcare.py -> structured_chat.py  (separate, nothing shared)
"""
import os
import pathlib

os.environ["APP_DOMAIN"] = "RERA"

_app = pathlib.Path(__file__).resolve().parent / "user_flow_rera.py"
exec(compile(_app.read_text(encoding="utf-8"), str(_app), "exec"))
