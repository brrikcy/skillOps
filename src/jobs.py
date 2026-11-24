"""Lambda entry point for background jobs (export, runbook processing). Called without waiting."""
from skillops.core import export, jobs  # noqa: F401  (each module registers its job kinds)
from skillops import livedata  # noqa: F401  (adds the live-data tables to the export)
from skillops.livedata import imports  # noqa: F401  (adding Live Data Simulator data from the page)
from skillops.runbook import service  # noqa: F401


def handler(event, context):
    jobs.run(event["job_id"])
    return {"ok": True}
