"""Who may do what. Checked on the server for every request (hiding a button is only a convenience)."""
from . import org
from .util import Forbidden


class Staff:
    def __init__(self, email, role, name, batch_ids=None):
        self.email = email
        self.role = role
        self.name = name
        self.batch_ids = set(batch_ids or [])

    @property
    def is_super(self):
        return self.role == "superadmin"

    @property
    def role_label(self):
        return "Supervisor" if self.is_super else "Trainer"


def staff_from_session(sess):
    """Build the person from their session. A trainer is re-checked every time, so turning
    an account off (or removing a batch) works at once."""
    if sess["role"] == "superadmin":
        return Staff(sess["email"], "superadmin", sess.get("name") or sess["email"])
    t = org.get_trainer(sess["email"], required=False)
    if not t or not t.get("enabled"):
        raise Forbidden("Your trainer account is turned off.", "Ask your supervisor.")
    return Staff(t["email"], "trainer", t["name"], org.trainer_batch_ids(t["email"]))


def require_super(staff):
    if not staff.is_super:
        raise Forbidden("Only the supervisor can do this.", "Ask your supervisor.")


def require_batch(staff, batch_id):
    """The batch, if this person may work with it. Trainers: assigned, and not archived."""
    b = org.get_batch(batch_id)
    if staff.is_super:
        return b
    if batch_id not in staff.batch_ids or not org.is_visible(b):
        raise Forbidden(f"{b['name']} is not assigned to you.", "Ask your supervisor.")
    return b


def visible_scope(staff):
    return org.scope(None if staff.is_super else staff.batch_ids)
