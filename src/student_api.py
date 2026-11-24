"""Lambda entry point for the Live Data Simulator student API (/v1/...)."""
from skillops.livedata.student_routes import handle


def handler(event, context):
    return handle(event)
