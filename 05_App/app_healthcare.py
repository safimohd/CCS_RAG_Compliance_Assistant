"""Healthcare deployment entry point.

Streamlit Community Cloud identifies an app by repo + branch + main file path,
and deploying the same script a second time just redeploys the first app. Two
apps therefore need two entry files. This is one of them; app_rera.py is the
other. Both run the same app.py.

The domain is set here rather than in Streamlit Secrets so it cannot be
misconfigured per deployment: _secret() in app.py reads os.environ first, so
this wins over any APP_DOMAIN in the secrets panel.

    Main file path on Community Cloud:  05_App/app_healthcare.py

Locally, `streamlit run app.py` still shows the domain selector.
"""
import os
import pathlib

os.environ["APP_DOMAIN"] = "Healthcare"

_app = pathlib.Path(__file__).resolve().parent / "app.py"
exec(compile(_app.read_text(encoding="utf-8"), str(_app), "exec"))
