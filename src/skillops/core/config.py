"""Settings. Everything comes from environment variables.

In AWS the SAM template sets them on each Lambda function.
On your laptop (tests, local_server.py, demo.py, admin.py) the starting code sets them.
"""
import os


def _get(name, default=""):
    return os.environ.get(name, default)


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def platform_name():
    """The one place the app's name comes from. Change PLATFORM_NAME to rename it."""
    return _get("PLATFORM_NAME", "SkillOps")


def live_data_name():
    """Name of the live-data module."""
    return _get("LIVE_DATA_NAME", "Live Data Simulator")


def company():
    return "Robomy"


def support_email():
    """Where people write for help (SUPPORT_EMAIL). Empty means no mail link is shown."""
    return _get("SUPPORT_EMAIL").strip()


def region():
    return _get("AWS_REGION") or _get("AWS_DEFAULT_REGION") or "ap-south-1"


TABLE_DEFAULTS = {
    "CORE": "skillops-core",
    "PROGRESS": "skillops-progress",
    "AUDIT": "skillops-audit",
}


def table(name):
    """DynamoDB table name. SAM passes the real names; locally we use simple defaults."""
    return _get(f"TABLE_{name}", TABLE_DEFAULTS.get(name, "skillops-" + name.lower().replace("_", "-")))


def runbook_bucket():
    return _get("RUNBOOK_BUCKET", "skillops-runbooks-local")


def web_bucket():
    return _get("WEB_BUCKET", "")


def user_pool_id():
    return _get("USER_POOL_ID", "")


def user_pool_client_id():
    return _get("USER_POOL_CLIENT_ID", "")


def jobs_function():
    return _get("JOBS_FUNCTION", "")


def mcq_secret_arn():
    return _get("MCQ_SECRET_ARN", "")


def local_mode():
    """True only on a laptop (local_server.py, tests). Never set in AWS."""
    return _get("SKILLOPS_LOCAL") == "1"


def session_hours():
    return _int("SESSION_HOURS", 12)


def public_url():
    """Optional fixed web address (custom domain). Empty = work it out from the request."""
    return _get("PUBLIC_URL", "").rstrip("/")


def version():
    """Changes on every deploy (SAM sets it); used to refresh cached CSS/JS."""
    return _get("APP_VERSION", "dev")
