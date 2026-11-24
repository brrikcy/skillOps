"""First set-up from a CSV file.

One row per line, any of these columns (headings are not case-sensitive):
  institution, institution_code, programme, course, project, batch, mcq_batch, trainer_name, trainer_email

What a row does depends on what it fills in:
  course (+ project)              Robomy's catalogue: makes the course, and the project in it (in row order)
  institution + programme + course   also selects that course for the programme (its projects come with it)
  institution + programme + project  (no course) adds a project of another course to the programme
  institution + programme + batch    makes the batch
  trainer_email (+ trainer_name)  makes the trainer, adds them to the institution, and links them to the batch
Everything is matched by name inside its parent, so running the same file twice changes nothing the second
time. Step 1 (plan) only reads and checks; step 2 (apply) makes the changes. Both walk the rows the same way.
"""
import csv
import io

from . import org
from .util import UserError

COLUMNS = ["institution", "institution_code", "programme", "course", "project", "batch", "mcq_batch",
           "trainer_name", "trainer_email"]
TEMPLATE = ("institution,institution_code,programme,course,project,batch,mcq_batch,trainer_name,trainer_email\n"
            ",,,Data Analytics,IPL,,,,\n"
            ",,,Data Analytics,Airtel,,,,\n"
            "DBI Skill Park,DBI,DBI OJT Sem 3 - Data Analyst,Data Analytics,,,,,\n"
            "DBI Skill Park,DBI,DBI OJT Sem 3 - Data Analyst,,,Batch A,A,Trainer One,trainer1@example.com\n")


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


class _Walk:
    """Walks the rows once. With apply=False nothing changes: what would be made is kept in memory, so later
    rows see it exactly as they would after applying."""

    def __init__(self, apply):
        self.apply, self.actions, self.errors = apply, [], []
        self.new = {}                          # dry run: (kind, key) -> a stand-in record

    def _made(self, kind, key, text, make):
        self.actions.append(text)
        if self.apply:
            return make()
        rec = {"id": f"new:{kind}:{key}", "name": key, "new": True, "courses": [], "extra_projects": []}
        self.new[(kind, key)] = rec
        return rec

    # -- lookups that also see what this walk has made (dry run)
    def course(self, name):
        hit = next((c for c in org.list_courses() if _key(c["name"]) == _key(name)), None)
        return hit or self.new.get(("course", _key(name)))

    def project(self, name):
        for c, js in org.all_projects():
            for j in js:
                if _key(j["name"]) == _key(name):
                    return dict(j, course=c)
        return self.new.get(("project", _key(name)))

    def inst(self, name):
        hit = next((i for i in org.list_institutions(True) if _key(i["name"]) == _key(name)), None)
        return hit or self.new.get(("inst", _key(name)))

    def prog(self, inst, name):
        if inst.get("new"):
            return self.new.get(("prog", inst["id"], _key(name)))
        hit = next((p for p in org.list_programmes(inst["id"], True) if _key(p["name"]) == _key(name)), None)
        return hit or self.new.get(("prog", inst["id"], _key(name)))

    def batch(self, prog, name):
        if not prog.get("new"):
            hit = next((b for b in org.list_batches(prog["id"], True) if _key(b["name"]) == _key(name)), None)
            if hit:
                return hit
        return self.new.get(("batch", prog["id"], _key(name)))

    # -- the walk
    def row(self, n, r):
        course = project = None
        if r.get("course"):
            course = self.course(r["course"])
            if not course:
                course = self._made("course", _key(r["course"]), f"Create course \"{r['course']}\"",
                                    lambda: org.get_course(org.create_course(r["course"])))
            if r.get("project"):
                project = self.project(r["project"])
                if project and project.get("course_id") not in (None, course["id"]) and not project.get("new"):
                    self.errors.append(f"Line {n}: project \"{r['project']}\" is already in course "
                                       f"\"{project['course']['name']}\".")
                    return
                if not project:
                    cid = course["id"]
                    project = self._made("project", _key(r["project"]),
                                         f"Add project \"{r['project']}\" to course {r['course']}",
                                         lambda: org.project_item(org.add_project(cid, r["project"])))
        needs_inst = any(r.get(c) for c in ("programme", "batch", "trainer_email"))
        if not r.get("institution"):
            if needs_inst:
                self.errors.append(f"Line {n}: a programme, batch or trainer needs the institution.")
            elif not r.get("course"):
                self.errors.append(f"Line {n}: fill in a course, or an institution.")
            return
        inst = self.inst(r["institution"])
        if not inst:
            code = r.get("institution_code", "")
            if not code:
                self.errors.append(f"Line {n}: a new institution needs institution_code (for example DBI).")
                return
            if not org.CODE_RE.match(code):
                self.errors.append(f"Line {n}: institution_code may use letters, digits, - and _ (up to 16).")
                return
            inst = self._made("inst", _key(r["institution"]),
                              f"Create institution \"{r['institution']}\" ({code.upper()})",
                              lambda: org.get_institution(org.create_institution(r["institution"], code)))
        prog = None
        if r.get("programme"):
            prog = self.prog(inst, r["programme"])
            if not prog:
                iid = inst["id"]
                prog = self._made("prog", _key(r["programme"]),
                                  f"Create programme \"{r['programme']}\" at {r['institution']}",
                                  lambda: org.get_programme(org.create_programme(iid, r["programme"])))
                self.new.pop(("prog", _key(r["programme"])), None)
                if not self.apply:
                    self.new[("prog", iid, _key(r["programme"]))] = prog
            if course and not r.get("project") and course["id"] not in (prog.get("courses") or []):
                self.actions.append(f"Select course {r['course']} for {r['programme']}")
                if self.apply:
                    org.set_programme_courses(prog["id"], list(prog.get("courses") or []) + [course["id"]])
                    prog = org.get_programme(prog["id"])
                else:
                    prog["courses"] = list(prog.get("courses") or []) + [course["id"]]
            if r.get("project") and not r.get("course"):
                project = self.project(r["project"])
                if not project:
                    self.errors.append(f"Line {n}: there is no project \"{r['project']}\". "
                                       "Give its course in the course column to make it.")
                    return
                in_prog = (not prog.get("new") and any(p["id"] == project["id"] for p in org.list_projects(prog["id"]))) \
                    or project.get("course_id") in (prog.get("courses") or []) or project["id"] in prog.get("extra_projects", [])
                if not in_prog:
                    self.actions.append(f"Add project {r['project']} to {r['programme']}")
                    if self.apply:
                        org.add_programme_project(prog["id"], project["id"])
                    else:
                        prog.setdefault("extra_projects", []).append(project["id"])
        batch = None
        if r.get("batch"):
            if not prog:
                self.errors.append(f"Line {n}: a batch needs its programme.")
                return
            batch = self.batch(prog, r["batch"])
            if not batch:
                pid = prog["id"]
                mcq = f", MCQ batch \"{r['mcq_batch']}\"" if r.get("mcq_batch") else ""
                batch = self._made("batch", _key(r["batch"]), f"Create batch \"{r['batch']}\" in {r['programme']}{mcq}",
                                   lambda: org.get_batch(org.create_batch(pid, r["batch"], r.get("mcq_batch", ""))))
                self.new.pop(("batch", _key(r["batch"])), None)
                if not self.apply:
                    self.new[("batch", pid, _key(r["batch"]))] = batch
        email = (r.get("trainer_email") or "").lower()
        if not email:
            return
        if not org.EMAIL_RE.match(email):
            self.errors.append(f"Line {n}: \"{email}\" is not a valid email.")
            return
        seen = self.new.get(("trainer", email))          # dry run: this trainer as the walk has left them
        if not org.get_trainer(email, required=False) and not seen:
            if not r.get("trainer_name"):
                self.errors.append(f"Line {n}: a new trainer needs trainer_name.")
                return
            self._made("trainer", email, f"Create trainer {r['trainer_name']} <{email}> "
                       "(Cognito emails a temporary password)",
                       lambda: org.create_trainer(email, r["trainer_name"]))
            seen = self.new[("trainer", email)] = {"insts": set(), "batches": set()} if not self.apply else None
        if self.apply:
            insts_now, batches_now = org.trainer_institution_ids(email), org.trainer_batch_ids(email)
        else:
            if seen is None:
                seen = self.new[("trainer", email)] = {"insts": set(org.trainer_institution_ids(email)),
                                                       "batches": set(org.trainer_batch_ids(email))}
            insts_now, batches_now = seen["insts"], seen["batches"]
        if inst["id"] not in insts_now:
            self.actions.append(f"Add {email} to {r['institution']}")
            if self.apply:
                org.add_trainer_to_institution(email, inst["id"])
            else:
                insts_now.add(inst["id"])
        if batch and batch["id"] not in batches_now:
            self.actions.append(f"Link {email} to {r['batch']} ({r['programme']})")
            if self.apply:
                org.associate(email, batch["id"])
            else:
                batches_now.add(batch["id"])


def plan(text):
    """Returns {"actions": [...], "errors": [...], "rows": n} without changing anything."""
    rows = read_rows(text)
    w = _Walk(apply=False)
    for n, r in rows:
        w.row(n, r)
    return {"actions": w.actions, "errors": w.errors, "rows": len(rows)}


def apply(text):
    """Make the changes. Safe to run twice."""
    rows = read_rows(text)
    w = _Walk(apply=True)
    for n, r in rows:
        w.row(n, r)
    return w.actions
