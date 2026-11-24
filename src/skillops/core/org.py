"""The structure of SkillOps.

Robomy owns the courses; each course holds projects in teaching order (each project uses one runbook).
Partner institutions have programmes; a programme takes the projects of the courses it selects, minus any
taken out, plus any added from other courses. Each programme has batches. Trainers are a list of their own:
a trainer is added to one or more institutions, and can then be linked to that institution's batches.

Archive flags hide things; batch links are switched off (active = False) so their history stays.
"""
import re

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from . import config, store, util
from .util import Conflict, NotFound, UserError

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,15}$")


def _cognito():
    return boto3.client("cognito-idp", region_name=config.region(),
                        config=Config(retries={"max_attempts": 5, "mode": "adaptive"},
                                      connect_timeout=5, read_timeout=10))


def _same(a, b):
    return " ".join(str(a).split()).lower() == " ".join(str(b).split()).lower()


def _check_unique(existing, name, what, where):
    for e in existing:
        if _same(e["name"], name):
            raise Conflict(f"{what} called \"{e['name']}\" already exists{where}.",
                           "Choose a different name, or open the existing one.")


# ================================================================== institutions

def create_institution(name, code):
    name = util.clean_text(name, "the institution name", 100)
    code = util.clean_text(code, "the short code", 16)
    if not CODE_RE.match(code):
        raise UserError("The short code may use letters, digits, - and _ (up to 16), for example DBI.")
    _check_unique(list_institutions(include_archived=True), name, "An institution", "")
    iid = util.new_id("i")
    store.put_new("CORE", {"pk": f"INST#{iid}", "sk": "META", "type": "institution", "id": iid,
                           "name": name, "code": code.upper(), "logo": "", "archived": False,
                           "created_at": util.now_iso(), "gsi1pk": "TYPE#INST", "gsi1sk": name.lower()})
    return iid


def get_institution(iid, required=True):
    item = store.get("CORE", f"INST#{iid}") if iid else None
    if not item and required:
        raise NotFound("This institution does not exist.", "Go back to the list of institutions.")
    return item


def list_institutions(include_archived=False):
    items = store.by_gsi1("TYPE#INST")
    return [i for i in items if include_archived or not i.get("archived")]


def update_institution(iid, name, code):
    inst = get_institution(iid)
    name = util.clean_text(name, "the institution name", 100)
    code = util.clean_text(code, "the short code", 16)
    if not CODE_RE.match(code):
        raise UserError("The short code may use letters, digits, - and _ (up to 16), for example DBI.")
    _check_unique([i for i in list_institutions(True) if i["id"] != iid], name, "An institution", "")
    store.update("CORE", f"INST#{iid}", "META", {"name": name, "code": code.upper(), "gsi1sk": name.lower()})
    return inst


def set_institution_logo(iid, key):
    get_institution(iid)
    store.update("CORE", f"INST#{iid}", "META", {"logo": key})


def set_archived(kind, item_id, archived):
    pk = {"institution": "INST#", "programme": "PROG#", "batch": "BATCH#"}[kind] + item_id
    if not store.get("CORE", pk):
        raise NotFound(f"This {kind} does not exist.")
    store.update("CORE", pk, "META", {"archived": bool(archived), "archived_at": util.now_iso() if archived else ""})


# ================================================================== programmes

def create_programme(iid, name):
    inst = get_institution(iid)
    name = util.clean_text(name, "the programme name", 120)
    _check_unique(list_programmes(iid, include_archived=True), name, "A programme", f" at {inst['name']}")
    pid = util.new_id("p")
    store.put_new("CORE", {"pk": f"PROG#{pid}", "sk": "META", "type": "programme", "id": pid, "inst_id": iid,
                           "name": name, "archived": False, "created_at": util.now_iso(),
                           "courses": [], "extra_projects": [], "removed_projects": [],
                           "gsi1pk": f"PROGS#{iid}", "gsi1sk": name.lower()})
    return pid


def get_programme(pid, required=True):
    item = store.get("CORE", f"PROG#{pid}") if pid else None
    if not item and required:
        raise NotFound("This programme does not exist.", "Go back to its institution.")
    return item


def list_programmes(iid=None, include_archived=False):
    if iid:
        items = store.by_gsi1(f"PROGS#{iid}")
    else:
        items = []
        for inst in list_institutions(include_archived=True):
            items.extend(store.by_gsi1(f"PROGS#{inst['id']}"))
    return [p for p in items if include_archived or not p.get("archived")]


def rename_programme(pid, name):
    prog = get_programme(pid)
    name = util.clean_text(name, "the programme name", 120)
    _check_unique([p for p in list_programmes(prog["inst_id"], True) if p["id"] != pid], name, "A programme", "")
    store.update("CORE", f"PROG#{pid}", "META", {"name": name, "gsi1sk": name.lower()})


# ================================================================== courses (Robomy's) and their projects

def create_course(name):
    name = util.clean_text(name, "the course name", 100)
    _check_unique(list_courses(), name, "A course", "")
    cid = util.new_id("c")
    store.put_new("CORE", {"pk": f"COURSE#{cid}", "sk": "META", "type": "course", "id": cid, "name": name,
                           "created_at": util.now_iso(), "gsi1pk": "TYPE#COURSE", "gsi1sk": name.lower()})
    return cid


def get_course(cid, required=True):
    item = store.get("CORE", f"COURSE#{cid}") if cid else None
    if not item and required:
        raise NotFound("This course does not exist.", "Go back to the list of courses.")
    return item


def list_courses():
    return sorted(store.by_gsi1("TYPE#COURSE"), key=lambda c: c["name"].lower())


def rename_course(cid, name):
    get_course(cid)
    name = util.clean_text(name, "the course name", 100)
    _check_unique([c for c in list_courses() if c["id"] != cid], name, "A course", "")
    store.update("CORE", f"COURSE#{cid}", "META", {"name": name, "gsi1sk": name.lower()})


def course_projects(cid):
    """A course's projects in teaching order."""
    return sorted(store.by_gsi1(f"COURSEPROJ#{cid}"), key=lambda p: (p.get("order", 0), p["name"].lower()))


def all_projects():
    """Every project, course by course: [(course, [projects])]."""
    return [(c, course_projects(c["id"])) for c in list_courses()]


def project_item(jid, required=True):
    """A project by its id, whatever programme (or none) uses it."""
    item = store.get("CORE", f"PROJECT#{jid}") if jid else None
    if not item and required:
        raise NotFound("This project does not exist.")
    return item


def _project_names():
    return [p for _, ps in all_projects() for p in ps]


def add_project(cid, name):
    get_course(cid)
    name = util.clean_text(name, "the project name", 80)
    # one name per project across all courses, so a programme that mixes courses never shows two "Airtel"s
    _check_unique(_project_names(), name, "A project", " (project names are unique across courses)")
    order = max([p.get("order", 0) for p in course_projects(cid)] or [0]) + 1
    jid = util.new_id("j")
    store.put_new("CORE", {"pk": f"PROJECT#{jid}", "sk": "META", "type": "project", "id": jid, "course_id": cid,
                           "name": name, "order": order, "runbook_id": "", "created_at": util.now_iso(),
                           "gsi1pk": f"COURSEPROJ#{cid}", "gsi1sk": f"{order:05d}"})
    return jid


def rename_project(jid, name):
    project_item(jid)
    name = util.clean_text(name, "the project name", 80)
    _check_unique([p for p in _project_names() if p["id"] != jid], name, "A project",
                  " (project names are unique across courses)")
    store.update("CORE", f"PROJECT#{jid}", "META", {"name": name})


def move_project(jid, direction):
    """Swap a project with its neighbour in its course ('up' or 'down')."""
    j = project_item(jid)
    items = course_projects(j["course_id"])
    ids = [p["id"] for p in items]
    i = ids.index(jid)
    k = i - 1 if direction == "up" else i + 1
    if k < 0 or k >= len(items):
        return
    order = list(range(1, len(items) + 1))          # re-number 1..n so the order is always clean, then swap
    order[i], order[k] = order[k], order[i]
    for p, o in zip(items, order):
        if p.get("order") != o:
            store.update("CORE", f"PROJECT#{p['id']}", "META", {"order": o, "gsi1sk": f"{o:05d}"})


def set_project_runbook(jid, runbook_id):
    project_item(jid)
    store.update("CORE", f"PROJECT#{jid}", "META", {"runbook_id": runbook_id or ""})


# ================================================================== a programme's projects

def _resolve(prog, include_removed=False):
    """The programme's projects in teaching order: the selected courses' projects (courses in the order the
    programme lists them), then the projects added from other courses. Each carries "source" ("course" or
    "added"), "course" (its course) and "removed" (taken out of this programme)."""
    removed = set(prog.get("removed_projects") or [])
    courses = {c["id"]: c for c in list_courses()}
    out, seen = [], set()
    for cid in prog.get("courses") or []:
        if cid not in courses:
            continue
        for p in course_projects(cid):
            if p["id"] in seen or (p["id"] in removed and not include_removed):
                continue
            seen.add(p["id"])
            out.append(dict(p, source="course", course=courses[cid], removed=p["id"] in removed))
    for jid in prog.get("extra_projects") or []:
        p = project_item(jid, required=False)
        if p and p["id"] not in seen:
            seen.add(p["id"])
            out.append(dict(p, source="added", course=courses.get(p.get("course_id"), {}), removed=False))
    return out


def list_projects(pid, include_archived=False):
    """The projects a programme teaches, in order (include_archived: also the ones taken out)."""
    prog = get_programme(pid, required=False)
    return _resolve(prog, include_removed=include_archived) if prog else []


def get_project(pid, proj_id, required=True):
    """A project of this programme (also one taken out, so its history stays readable)."""
    prog = get_programme(pid, required=False)
    hit = next((p for p in _resolve(prog, include_removed=True) if p["id"] == proj_id), None) if prog else None
    if not hit and required:
        raise NotFound("This project is not part of this programme.")
    return hit


def set_programme_courses(pid, course_ids):
    """Select exactly these courses (kept courses keep their place; new ones go at the end)."""
    prog = get_programme(pid)
    wanted = [c for c in dict.fromkeys(course_ids) if c]
    for cid in wanted:
        get_course(cid)
    current = [c for c in prog.get("courses") or [] if c in wanted]
    current += [c for c in wanted if c not in current]
    # a project added by hand that now comes with a selected course is no longer an extra
    in_courses = {p["id"] for cid in current for p in course_projects(cid)}
    extras = [j for j in prog.get("extra_projects") or [] if j not in in_courses]
    removed = [j for j in prog.get("removed_projects") or [] if j in in_courses]
    store.update("CORE", f"PROG#{pid}", "META", {"courses": current, "extra_projects": extras,
                                                 "removed_projects": removed})
    return current


def move_programme_course(pid, cid, direction):
    prog = get_programme(pid)
    cs = list(prog.get("courses") or [])
    if cid not in cs:
        raise NotFound("This course is not selected for this programme.")
    i = cs.index(cid)
    k = i - 1 if direction == "up" else i + 1
    if 0 <= k < len(cs):
        cs[i], cs[k] = cs[k], cs[i]
        store.update("CORE", f"PROG#{pid}", "META", {"courses": cs})


def add_programme_project(pid, jid):
    """Add one project: a project taken out comes back; a project of another course is added at the end."""
    prog = get_programme(pid)
    project_item(jid)
    removed = list(prog.get("removed_projects") or [])
    if jid in removed:
        removed.remove(jid)
        store.update("CORE", f"PROG#{pid}", "META", {"removed_projects": removed})
        return "back"
    if any(p["id"] == jid for p in _resolve(prog)):
        return "already"
    store.update("CORE", f"PROG#{pid}", "META", {"extra_projects": list(prog.get("extra_projects") or []) + [jid]})
    return "added"


def remove_programme_project(pid, jid):
    """Take one project out of the programme. Its ticks stay, and it can be put back."""
    prog = get_programme(pid)
    hit = next((p for p in _resolve(prog) if p["id"] == jid), None)
    if not hit:
        raise NotFound("This project is not part of this programme.")
    if hit["source"] == "added":
        extras = [j for j in prog.get("extra_projects") or [] if j != jid]
        store.update("CORE", f"PROG#{pid}", "META", {"extra_projects": extras})
    else:
        store.update("CORE", f"PROG#{pid}", "META",
                     {"removed_projects": list(prog.get("removed_projects") or []) + [jid]})
    return hit


def programmes_using_course(cid):
    return [p for p in list_programmes(include_archived=True) if cid in (p.get("courses") or [])]


# ================================================================== batches

MCQ_BATCH_RE = re.compile(r"^[A-Za-z0-9 _\-]{0,40}$")


def create_batch(pid, name, mcq_batch=""):
    prog = get_programme(pid)
    name = util.clean_text(name, "the batch name", 60)
    mcq_batch = util.clean_text(mcq_batch, "the MCQ batch name", 40, required=False)
    if not MCQ_BATCH_RE.match(mcq_batch):
        raise UserError("The MCQ batch name may use letters, digits, spaces, - and _.")
    _check_unique(list_batches(pid, include_archived=True), name, "A batch", f" in {prog['name']}")
    bid = util.new_id("b")
    store.put_new("CORE", {"pk": f"BATCH#{bid}", "sk": "META", "type": "batch", "id": bid, "prog_id": pid,
                           "inst_id": prog["inst_id"], "name": name, "mcq_batch": mcq_batch, "archived": False,
                           "versions": {}, "created_at": util.now_iso(),
                           "gsi1pk": f"BATCHES#{pid}", "gsi1sk": name.lower()})
    return bid


def get_batch(bid, required=True):
    item = store.get("CORE", f"BATCH#{bid}") if bid else None
    if not item and required:
        raise NotFound("This batch does not exist.", "Go back to the home page.")
    return item


def list_batches(pid=None, include_archived=False):
    if pid:
        items = store.by_gsi1(f"BATCHES#{pid}")
    else:
        items = []
        for p in list_programmes(include_archived=True):
            items.extend(store.by_gsi1(f"BATCHES#{p['id']}"))
    return [b for b in items if include_archived or not b.get("archived")]


def update_batch(bid, name, mcq_batch):
    b = get_batch(bid)
    name = util.clean_text(name, "the batch name", 60)
    mcq_batch = util.clean_text(mcq_batch, "the MCQ batch name", 40, required=False)
    if not MCQ_BATCH_RE.match(mcq_batch):
        raise UserError("The MCQ batch name may use letters, digits, spaces, - and _.")
    _check_unique([x for x in list_batches(b["prog_id"], True) if x["id"] != bid], name, "A batch",
                  " in this programme")
    store.update("CORE", f"BATCH#{bid}", "META", {"name": name, "mcq_batch": mcq_batch, "gsi1sk": name.lower()})


# ================================================================== trainers

def get_trainer(email, required=True):
    email = (email or "").strip().lower()
    item = store.get("CORE", f"TRAINER#{email}", consistent=True) if email else None
    if not item and required:
        raise NotFound("This trainer does not exist.", "Go back to the list of trainers.")
    return item


def list_trainers():
    return sorted(store.by_gsi1("TYPE#TRAINER"), key=lambda t: t["name"].lower())


def create_trainer(email, name, inst_id=None):
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 120:
        raise UserError("Please give a valid email address.")
    name = util.clean_text(name, "the trainer's name", 80)
    if get_trainer(email, required=False):
        raise Conflict(f"{email} is already a trainer.", "Open the trainer from the list instead.")
    if inst_id:
        get_institution(inst_id)
    pool = config.user_pool_id()
    if pool:
        c = _cognito()
        try:
            c.admin_create_user(
                UserPoolId=pool, Username=email, DesiredDeliveryMediums=["EMAIL"],
                UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"},
                                {"Name": "name", "Value": name}])
        except ClientError as e:
            if e.response["Error"]["Code"] != "UsernameExistsException":
                raise
        c.admin_add_user_to_group(UserPoolId=pool, Username=email, GroupName="trainer")
    store.put_new("CORE", {"pk": f"TRAINER#{email}", "sk": "META", "type": "trainer", "email": email,
                           "name": name, "enabled": True, "created_at": util.now_iso(),
                           "gsi1pk": "TYPE#TRAINER", "gsi1sk": name.lower()})
    if inst_id:
        add_trainer_to_institution(email, inst_id)
    return email


def add_trainer_to_institution(email, iid):
    """A trainer works with an institution: they can then be linked to its batches. Repeating is harmless."""
    t = get_trainer(email)
    get_institution(iid)
    store.t("CORE").put_item(Item={
        "pk": f"TRAINER#{t['email']}", "sk": f"INST#{iid}", "type": "member", "email": t["email"], "inst_id": iid,
        "since": util.now_iso(), "gsi1pk": f"INSTTRAINERS#{iid}", "gsi1sk": t["email"]})


def remove_trainer_from_institution(email, iid):
    """The trainer no longer works with this institution: they are unlinked from its batches too
    (their past ticks are kept). Returns the batches they were unlinked from."""
    email = (email or "").strip().lower()
    store.t("CORE").delete_item(Key={"pk": f"TRAINER#{email}", "sk": f"INST#{iid}"})
    gone = []
    for bid in trainer_batch_ids(email):
        b = get_batch(bid, required=False)
        if b and b.get("inst_id") == iid:
            dissociate(email, bid)
            gone.append(b)
    return gone


def trainer_institution_ids(email):
    return {i["inst_id"] for i in store.by_pk("CORE", f"TRAINER#{email}", "INST#", consistent=True)}


def institution_trainers(iid):
    """The trainers who work with an institution, by name."""
    out = []
    for m in store.by_gsi1(f"INSTTRAINERS#{iid}"):
        t = get_trainer(m["email"], required=False)
        if t:
            out.append(t)
    return sorted(out, key=lambda t: t["name"].lower())


def trainer_directory():
    """Every trainer with their institutions (dicts) and batches (dicts), by name."""
    insts = {i["id"]: i for i in list_institutions(include_archived=True)}
    batches = {b["id"]: b for b in list_batches(include_archived=True)}
    out = []
    for t in list_trainers():
        t["institutions"] = sorted((insts[i] for i in trainer_institution_ids(t["email"]) if i in insts),
                                   key=lambda i: i["name"].lower())
        t["batches"] = sorted((batches[b] for b in trainer_batch_ids(t["email"]) if b in batches),
                              key=lambda b: b["name"].lower())
        out.append(t)
    return out


def rename_trainer(email, name):
    get_trainer(email)
    name = util.clean_text(name, "the trainer's name", 80)
    store.update("CORE", f"TRAINER#{email}", "META", {"name": name, "gsi1sk": name.lower()})


def set_trainer_enabled(email, enabled):
    get_trainer(email)
    store.update("CORE", f"TRAINER#{email}", "META", {"enabled": bool(enabled)})
    pool = config.user_pool_id()
    if pool:
        c = _cognito()
        if enabled:
            c.admin_enable_user(UserPoolId=pool, Username=email)
        else:
            c.admin_disable_user(UserPoolId=pool, Username=email)
            try:
                c.admin_user_global_sign_out(UserPoolId=pool, Username=email)
            except ClientError:
                pass


def resend_invitation(email):
    """For a trainer who has not signed in yet: a new temporary password by email."""
    get_trainer(email)
    pool = config.user_pool_id()
    if not pool:
        return "Local mode: no email is sent."
    try:
        _cognito().admin_create_user(UserPoolId=pool, Username=email, MessageAction="RESEND",
                                     DesiredDeliveryMediums=["EMAIL"])
        return "A new temporary password has been emailed."
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("UnsupportedUserStateException", "NotAuthorizedException"):
            raise UserError("This trainer has already chosen a password.",
                            "Use 'Reset password' instead, or ask them to use 'Forgot password'.")
        if code == "UserNotFoundException":
            # The record came back from an export into a new stack: make the sign-in account again.
            ensure_account(get_trainer(email))
            return "The sign-in account was made again; a temporary password has been emailed."
        raise


def ensure_account(t):
    """Make sure a trainer record has a Cognito sign-in account (after a restore into a new stack).
    Returns True if one was made."""
    pool = config.user_pool_id()
    if not pool:
        return False
    c = _cognito()
    made = False
    try:
        c.admin_get_user(UserPoolId=pool, Username=t["email"])
    except ClientError as e:
        if e.response["Error"]["Code"] != "UserNotFoundException":
            raise
        c.admin_create_user(
            UserPoolId=pool, Username=t["email"], DesiredDeliveryMediums=["EMAIL"],
            UserAttributes=[{"Name": "email", "Value": t["email"]}, {"Name": "email_verified", "Value": "true"},
                            {"Name": "name", "Value": t["name"]}])
        made = True
    c.admin_add_user_to_group(UserPoolId=pool, Username=t["email"], GroupName="trainer")
    if not t.get("enabled", True):
        c.admin_disable_user(UserPoolId=pool, Username=t["email"])
    return made


def reset_password(email):
    """For a trainer who has signed in before: they must set a new password with an emailed code."""
    get_trainer(email)
    pool = config.user_pool_id()
    if not pool:
        return "Local mode: no email is sent."
    try:
        _cognito().admin_reset_user_password(UserPoolId=pool, Username=email)
        return "A reset code has been emailed. At the next sign-in they choose a new password."
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NotAuthorizedException", "InvalidParameterException"):
            raise UserError("This trainer has not chosen a password yet.",
                            "Use 'Send the invitation again' instead.")
        raise


# ================================================================== associations

def associate(email, bid):
    t = get_trainer(email)
    b = get_batch(bid)
    if b.get("inst_id") not in trainer_institution_ids(t["email"]):
        inst = get_institution(b.get("inst_id"), required=False) or {}
        raise UserError(f"{t['name']} is not a trainer at {inst.get('name', 'this institution')}.",
                        "Add them to the institution first (on the institution's page), then link the batch.")
    store.t("CORE").put_item(Item={
        "pk": f"TRAINER#{t['email']}", "sk": f"BATCH#{bid}", "type": "assoc", "email": t["email"], "batch_id": bid,
        "active": True, "since": util.now_iso(), "gsi1pk": f"BATCHTRAINERS#{bid}", "gsi1sk": t["email"]})
    return t, b


def dissociate(email, bid):
    item = store.get("CORE", f"TRAINER#{email}", f"BATCH#{bid}")
    if not item or not item.get("active"):
        return
    store.update("CORE", f"TRAINER#{email}", f"BATCH#{bid}", {"active": False, "until": util.now_iso()})


def trainer_batch_ids(email):
    items = store.by_pk("CORE", f"TRAINER#{email}", "BATCH#", consistent=True)
    return {i["batch_id"] for i in items if i.get("active")}


def batch_trainers(bid):
    """Active trainers of a batch: [{email, name, enabled}]."""
    out = []
    for a in store.by_gsi1(f"BATCHTRAINERS#{bid}"):
        if not a.get("active"):
            continue
        t = get_trainer(a["email"], required=False)
        if t:
            out.append(t)
    return sorted(out, key=lambda t: t["name"].lower())


def set_trainer_batches(email, wanted_ids):
    """Make the trainer's active batches exactly wanted_ids. Returns (added, removed)."""
    get_trainer(email)
    wanted = set(wanted_ids)
    for bid in wanted:
        get_batch(bid)
    current = trainer_batch_ids(email)
    for bid in wanted - current:
        associate(email, bid)
    for bid in current - wanted:
        dissociate(email, bid)
    return sorted(wanted - current), sorted(current - wanted)


# ================================================================== what a person can see

def is_visible(batch, insts=None, progs=None):
    """A batch is visible to trainers only if it, its programme and its institution are not archived."""
    if batch.get("archived"):
        return False
    prog = (progs or {}).get(batch["prog_id"]) or get_programme(batch["prog_id"], required=False)
    if not prog or prog.get("archived"):
        return False
    inst = (insts or {}).get(batch["inst_id"]) or get_institution(batch["inst_id"], required=False)
    return bool(inst) and not inst.get("archived")


def scope(batch_ids=None, include_archived=False):
    """The tree institution -> programmes -> batches.

    batch_ids=None means everything (superadmin). Otherwise only those batches (a trainer).
    Archived items are left out unless include_archived.
    """
    insts = {i["id"]: i for i in list_institutions(include_archived=True)}
    progs = {p["id"]: p for p in list_programmes(include_archived=True)}
    tree = {}
    for b in list_batches(include_archived=True):
        if batch_ids is not None and b["id"] not in batch_ids:
            continue
        if not include_archived and not is_visible(b, insts, progs):
            continue
        prog, inst = progs.get(b["prog_id"]), insts.get(b["inst_id"])
        if not prog or not inst:
            continue
        i = tree.setdefault(inst["id"], {"institution": inst, "programmes": {}})
        p = i["programmes"].setdefault(prog["id"], {"programme": prog, "batches": []})
        p["batches"].append(b)
    out = []
    for i in sorted(tree.values(), key=lambda x: x["institution"]["name"].lower()):
        progs_out = sorted(i["programmes"].values(), key=lambda x: x["programme"]["name"].lower())
        for p in progs_out:
            p["batches"].sort(key=lambda b: b["name"].lower())
        out.append({"institution": i["institution"], "programmes": progs_out})
    return out


def batch_context(bid):
    """Batch with its programme and institution (for page headers)."""
    b = get_batch(bid)
    return {"batch": b, "programme": get_programme(b["prog_id"], required=False) or {},
            "institution": get_institution(b["inst_id"], required=False) or {}}
