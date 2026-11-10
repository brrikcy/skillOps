"""First set-up from a CSV file.

One row per line, any of these columns (headings are not case-sensitive):
  institution, institution_code, programme, project, batch, mcq_batch, trainer_name, trainer_email

Each row creates what does not exist yet (matched by name inside its parent) and links the
trainer to the batch. A row can be partial: "institution, programme, project" rows add projects
in the order they appear. Running the same file twice changes nothing the second time.
Step 1 (plan) only reads and checks. Step 2 (apply) makes the changes.
"""
import csv
import io

from . import org
from .util import UserError

COLUMNS = ["institution", "institution_code", "programme", "project", "batch", "mcq_batch",
           "trainer_name", "trainer_email"]
TEMPLATE = ("institution,institution_code,programme,project,batch,mcq_batch,trainer_name,trainer_email\n"
            "DBI Skill Park,DBI,DBI OJT Sem 3 - Data Analyst,IPL,,,,\n"
            "DBI Skill Park,DBI,DBI OJT Sem 3 - Data Analyst,Airtel,,,,\n"
            "DBI Skill Park,DBI,DBI OJT Sem 3 - Data Analyst,,Batch A,A,Trainer One,trainer1@example.com\n")


def _norm(s):
    return " ".join(str(s or "").split())


def _key(s):
    return _norm(s).lower()


def read_rows(text):
    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise UserError("The file is empty.", "Use the template: the first line must be the column headings.")
    heads = {h: _key(h).replace(" ", "_") for h in reader.fieldnames if h}
    unknown = [h for h, k in heads.items() if k not in COLUMNS]
    if unknown:
        raise UserError(f"Unknown column(s): {', '.join(unknown)}.", "Allowed columns: " + ", ".join(COLUMNS) + ".")
    rows = []
    for n, raw in enumerate(reader, start=2):
        row = {heads[h]: _norm(v) for h, v in raw.items() if h in heads}
        if any(row.values()):
            rows.append((n, row))
    if not rows:
        raise UserError("The file has headings but no rows.")
    if len(rows) > 2000:
        raise UserError("The file has more than 2,000 rows.", "Split it into smaller files.")
    return rows


def plan(text):
    """Returns {"actions": [...], "errors": [...]} without changing anything."""
    rows = read_rows(text)
    actions, errors = [], []
    insts = {_key(i["name"]): i for i in org.list_institutions(include_archived=True)}
    progs = {}
    for p in org.list_programmes(include_archived=True):
        progs[(p["inst_id"], _key(p["name"]))] = p
    planned_insts, planned_progs, planned_projects, planned_batches = set(), set(), set(), set()
    planned_trainers, planned_links = set(), set()
    trainers = {t["email"]: t for t in org.list_trainers()}

    for n, r in rows:
        inst_name = r.get("institution", "")
        if not inst_name:
            errors.append(f"Line {n}: the institution is empty.")
            continue
        ik = _key(inst_name)
        inst = insts.get(ik)
        if not inst and ik not in planned_insts:
            if not r.get("institution_code"):
                errors.append(f"Line {n}: a new institution needs institution_code (for example DBI).")
                continue
            if not org.CODE_RE.match(r["institution_code"]):
                errors.append(f"Line {n}: institution_code may use letters, digits, - and _ (up to 16).")
                continue
            planned_insts.add(ik)
            actions.append(f"Create institution \"{inst_name}\" ({r['institution_code'].upper()})")
        prog_name = r.get("programme", "")
        if not prog_name:
            if any(r.get(c) for c in ("project", "batch", "trainer_email")):
                errors.append(f"Line {n}: a project, batch or trainer needs a programme.")
            continue
        pk_ = (inst["id"] if inst else ik, _key(prog_name))
        existing_prog = progs.get(pk_) if inst else None
        if not existing_prog and pk_ not in planned_progs:
            planned_progs.add(pk_)
            actions.append(f"Create programme \"{prog_name}\" at {inst_name}")
        if r.get("project"):
            existing = {_key(p["name"]) for p in (org.list_projects(existing_prog["id"]) if existing_prog else [])}
            jk = (pk_, _key(r["project"]))
            if _key(r["project"]) not in existing and jk not in planned_projects:
                planned_projects.add(jk)
                actions.append(f"Add project \"{r['project']}\" to {prog_name}")
        if r.get("batch"):
            existing = {_key(b["name"]) for b in (org.list_batches(existing_prog["id"], True) if existing_prog else [])}
            bk = (pk_, _key(r["batch"]))
            if _key(r["batch"]) not in existing and bk not in planned_batches:
                planned_batches.add(bk)
                mcq = f", MCQ batch \"{r['mcq_batch']}\"" if r.get("mcq_batch") else ""
                actions.append(f"Create batch \"{r['batch']}\" in {prog_name}{mcq}")
        email = (r.get("trainer_email") or "").lower()
        if email:
            if not org.EMAIL_RE.match(email):
                errors.append(f"Line {n}: \"{email}\" is not a valid email.")
                continue
            if email not in trainers and email not in planned_trainers:
                if not r.get("trainer_name"):
                    errors.append(f"Line {n}: a new trainer needs trainer_name.")
                    continue
                planned_trainers.add(email)
                actions.append(f"Create trainer {r['trainer_name']} <{email}> (Cognito emails a temporary password)")
            if not r.get("batch"):
                errors.append(f"Line {n}: a trainer row needs the batch to link them to.")
                continue
            lk = (email, pk_, _key(r["batch"]))
            if lk not in planned_links:
                planned_links.add(lk)
                linked = False
                if existing_prog and email in trainers:
                    b = next((b for b in org.list_batches(existing_prog["id"], True)
                              if _key(b["name"]) == _key(r["batch"])), None)
                    linked = bool(b) and b["id"] in org.trainer_batch_ids(email)
                if not linked:
                    actions.append(f"Link {email} to {r['batch']} ({prog_name})")
    return {"actions": actions, "errors": errors, "rows": len(rows)}


def apply(text):
    """Make the changes. Safe to run twice."""
    rows = read_rows(text)
    done = []
    for n, r in rows:
        if not r.get("institution"):
            continue
        inst = next((i for i in org.list_institutions(True) if _key(i["name"]) == _key(r["institution"])), None)
        if not inst:
            iid = org.create_institution(r["institution"], r.get("institution_code") or "X")
            inst = org.get_institution(iid)
            done.append(f"Created institution {inst['name']}")
        if not r.get("programme"):
            continue
        prog = next((p for p in org.list_programmes(inst["id"], True) if _key(p["name"]) == _key(r["programme"])), None)
        if not prog:
            prog = org.get_programme(org.create_programme(inst["id"], r["programme"]))
            done.append(f"Created programme {prog['name']}")
        if r.get("project") and not any(_key(p["name"]) == _key(r["project"]) for p in org.list_projects(prog["id"])):
            org.add_project(prog["id"], r["project"])
            done.append(f"Added project {r['project']}")
        batch = None
        if r.get("batch"):
            batch = next((b for b in org.list_batches(prog["id"], True) if _key(b["name"]) == _key(r["batch"])), None)
            if not batch:
                batch = org.get_batch(org.create_batch(prog["id"], r["batch"], r.get("mcq_batch", "")))
                done.append(f"Created batch {batch['name']}")
        email = (r.get("trainer_email") or "").lower()
        if email and batch:
            if not org.get_trainer(email, required=False):
                org.create_trainer(email, r.get("trainer_name") or email)
                done.append(f"Created trainer {email}")
            if batch["id"] not in org.trainer_batch_ids(email):
                org.associate(email, batch["id"])
                done.append(f"Linked {email} to {batch['name']}")
    return done
