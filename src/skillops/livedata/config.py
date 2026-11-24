"""Settings for Live Data Simulator (the live-data module). Everything comes from environment variables.

In AWS, the SAM template sets these on each Lambda function.
On your laptop (tests, demo, admin.py) they are set by the code that starts things.
"""
import os


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def platform_name():
    """Name of the live-data module (shown in API answers). Change LIVE_DATA_NAME to rename it."""
    return os.environ.get("LIVE_DATA_NAME", "Live Data Simulator")


def table(name):
    """DynamoDB table names. They are shared with SkillOps' own settings (TABLE_LIVE_ROWS etc.)."""
    from ..core import config as core_config
    return core_config.table("LIVE_" + name)


def rate_limit_per_minute():
    return _int("RATE_LIMIT_PER_MINUTE", 30)


def register_limit_per_ip():
    return _int("REGISTER_LIMIT_PER_IP_PER_MINUTE", 30)


def register_limit_per_code():
    return _int("REGISTER_LIMIT_PER_CODE_PER_MINUTE", 120)


def default_page_size():
    return _int("PAGE_SIZE", 5000)


def fetchlog_ttl_days():
    return _int("FETCHLOG_TTL_DAYS", 180)


def public_api_url():
    """Address students put in SIMLAB_API_URL. Empty means 'work it out from the request'."""
    return os.environ.get("PUBLIC_API_URL", "").rstrip("/")


def web_url():
    return os.environ.get("WEB_URL", "").rstrip("/")


def user_pool_id():
    return os.environ.get("USER_POOL_ID", "")


def secret_arn():
    return os.environ.get("SERVER_SECRET_ARN", "")
