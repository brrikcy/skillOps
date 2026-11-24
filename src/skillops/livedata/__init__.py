"""Live Data Simulator: the live-data simulation engine (copied from ROBOMY DSE).

Its tables are registered with SkillOps' store, so tests, local mode, health checks and the
export see them too. Student-facing parts (the /v1 API and the register, docs and status pages)
are public; staff parts use SkillOps sign-in, roles, batches and audit log.
"""
from ..core import export as _export
from ..core import store as _core_store
from . import store as _store

_core_store.EXTRA_TABLES.update({"LIVE_" + k: v for k, v in _store.table_definitions().items()})
# Exported (rows are not: keep the .db files you uploaded; they can be loaded again).
for _name in ("LIVE_CONFIG", "LIVE_STUDENTS", "LIVE_FETCHLOG"):
    if _name not in _export.EXPORT_TABLES:
        _export.EXPORT_TABLES.append(_name)
