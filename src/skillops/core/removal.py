"""Deleting courses, projects, institutions, programmes, batches and trainers for good.

Archive hides and keeps; Delete removes. A delete takes everything that belongs to the thing with it:
  course      its projects (and so they leave every programme that used them)
  project     the project, its place in every programme, and which runbook version each batch used for it
  batch       its ticks, notes, tick history, session plans, class days, trainer links, Live Data Simulator
              students and join codes
  programme   every batch in it (the courses and projects stay: they are Robomy's)
  institution its programmes (and so their batches), its trainer list, its MCQ connection and its logo
              (the trainers themselves stay, with their other institutions)
  trainer     the sign-in account, the institution memberships and the batch links (their ticks keep their name)
The audit log keeps a line for every delete. Runbooks are not touched (they belong to the shared library).
"""
from boto3.dynamodb.conditions import Attr, Key

from . import config, org, store
from .util import NotFound


def _delete_keys(table_name, keys):
    with store.t(table_name).batch_writer() as w:
        for k in keys:
            w.delete_item(Key=k)


def _pk_items(table_name, pk):
    return store.query_all(table_name, KeyConditionExpression=Key("pk").eq(pk), ProjectionExpression="pk, sk")


def delete_batch(bid):
    b = org.get_batch(bid)
    # progress: every version the batch ever used, and its tick history
    prog_keys = [{"pk": i["pk"], "sk": i["sk"]} for i in store.scan_all(
        "PROGRESS", FilterExpression=Attr("pk").begins_with(f"B#{bid}#V#"), ProjectionExpression="pk, sk")]
    prog_keys += [{"pk": i["pk"], "sk": i["sk"]} for i in _pk_items("PROGRESS", f"EVT#{bid}")]
    _delete_keys("PROGRESS", prog_keys)
    # core: the batch itself (META, class days ...), its session plans, trainer links
    core_keys = [{"pk": i["pk"], "sk": i["sk"]} for i in _pk_items("CORE", f"BATCH#{bid}")]
    core_keys += [{"pk": i["pk"], "sk": i["sk"]} for i in _pk_items("CORE", f"PLAN#{bid}")]
    core_keys += [{"pk": a["pk"], "sk": a["sk"]} for a in store.by_gsi1(f"BATCHTRAINERS#{bid}")]
    _delete_keys("CORE", core_keys)
    _delete_live_students(bid)
    return b


def _delete_live_students(bid):
    try:
        from ..livedata import students
        from ..livedata import store as live_store
    except ImportError:
        return
    table = live_store.t("STUDENTS")
    for s in students.list_students(bid):
        table.delete_item(Key={"pk": f"STU#{s['student_id']}"})
        if s.get("key_hash"):
            table.delete_item(Key={"pk": f"KEY#{s['key_hash']}"})
    for c in students.list_codes():
        if c["batch"] == bid:
            students.close_code(c["code"])


def delete_programme(pid):
    p = org.get_programme(pid)
    for b in org.list_batches(pid, include_archived=True):
        delete_batch(b["id"])
    _delete_keys("CORE", [{"pk": i["pk"], "sk": i["sk"]} for i in _pk_items("CORE", f"PROG#{pid}")])
    return p


def delete_project(jid):
    j = org.project_item(jid)
    store.t("CORE").delete_item(Key={"pk": f"PROJECT#{jid}", "sk": "META"})
    for p in org.list_programmes(include_archived=True):           # out of every programme's own lists
        extras = [x for x in p.get("extra_projects") or [] if x != jid]
        removed = [x for x in p.get("removed_projects") or [] if x != jid]
        if extras != (p.get("extra_projects") or []) or removed != (p.get("removed_projects") or []):
            store.update("CORE", f"PROG#{p['id']}", "META", {"extra_projects": extras, "removed_projects": removed})
    for b in org.list_batches(include_archived=True):             # forget which version batches used for it
        if jid in (b.get("versions") or {}):
            versions = dict(b["versions"])
            versions.pop(jid)
            store.update("CORE", f"BATCH#{b['id']}", "META", {"versions": versions})
        if b.get("current_project") == jid:
            store.update("CORE", f"BATCH#{b['id']}", "META", {"current_project": ""})
    return j


def delete_course(cid):
    c = org.get_course(cid)
    for j in org.course_projects(cid):
        delete_project(j["id"])
    for p in org.programmes_using_course(cid):
        store.update("CORE", f"PROG#{p['id']}", "META", {"courses": [x for x in p["courses"] if x != cid]})
    store.t("CORE").delete_item(Key={"pk": f"COURSE#{cid}", "sk": "META"})
    return c


def delete_institution(iid):
    inst = org.get_institution(iid)
    for p in org.list_programmes(iid, include_archived=True):
        delete_programme(p["id"])
    keys = [{"pk": f"INST#{iid}", "sk": "META"}, {"pk": f"MCQCFG#{iid}", "sk": "META"},
            {"pk": f"MCQSECRET#{iid}", "sk": "META"}, {"pk": f"MCQDOWN#{iid}", "sk": "META"}]
    keys += [{"pk": i["pk"], "sk": i["sk"]} for i in store.scan_all(
        "CORE", FilterExpression=Attr("pk").begins_with(f"MCQCACHE#{iid}"), ProjectionExpression="pk, sk")]
    _delete_keys("CORE", keys)
    if inst.get("logo"):
        try:
            store.s3().delete_object(Bucket=config.runbook_bucket(), Key=inst["logo"])
        except Exception:  # noqa: BLE001  (a missing logo file is not a reason to stop)
            pass
    _delete_keys("CORE", [{"pk": m["pk"], "sk": m["sk"]} for m in store.by_gsi1(f"INSTTRAINERS#{iid}")])
    return inst


def delete_trainer(email):
    t = org.get_trainer(email)
    pool = config.user_pool_id()
    if pool:
        from botocore.exceptions import ClientError
        try:
            org._cognito().admin_delete_user(UserPoolId=pool, Username=email)
        except ClientError as e:
            if e.response["Error"]["Code"] != "UserNotFoundException":
                raise
    _delete_keys("CORE", [{"pk": i["pk"], "sk": i["sk"]} for i in _pk_items("CORE", f"TRAINER#{email}")])
    # sessions of this person end at once
    _delete_keys("CORE", [{"pk": i["pk"], "sk": i["sk"]} for i in store.scan_all(
        "CORE", FilterExpression=Attr("pk").begins_with("SESSION#") & Attr("email").eq(email),
        ProjectionExpression="pk, sk")])
    return t


def check_name(typed, real, what):
    from .util import UserError
    if (typed or "").strip().lower() != (real or "").strip().lower():
        raise UserError(f"Type the {what}'s name to confirm.", f'Type "{real}" in the box, then press Delete.')


__all__ = ["delete_batch", "delete_programme", "delete_project", "delete_course", "delete_institution",
           "delete_trainer",
           "check_name", "NotFound"]
