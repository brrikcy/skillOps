"""Runbook: structure detection, mapping, templates, preview edits, publish, rendering, navigator,
per-batch ticks (repeat-safe and simultaneous), notes, search, versions. Plus the Airtel acceptance test."""
import json
import os
import re
import threading

import pytest

import docx_factory
from helpers import Browser, body, setup_org
from skillops.core import jobs, org
from skillops.core.perms import Staff
from skillops.runbook import detect, docxread, progress, render, service, tree

AIRTEL = os.environ.get("SKILLOPS_SAMPLE_RUNBOOK", "")


@pytest.fixture(autouse=True)
def inline_jobs():
    jobs.INLINE = True
    yield
    jobs.INLINE = False


def publish_runbook(data, name="Sample", template=""):
    rid = service.create_runbook(name)
    vid = service.add_version(rid, "sample.docx", data, "boss@robomy.test")
    v = service.get_version(vid)
    assert v["status"] == "mapping", v
    mapping, checkable = detect.suggest(v["patterns"])
    service.save_mapping(vid, mapping, checkable, [], template_name=template)
    service.publish(vid, "boss@robomy.test")
    assert service.get_version(vid)["status"] == "published"
    return rid, vid


# ------------------------------------------------------------------ detection

def test_detector_finds_levels_and_rejects_traps():
    d = docxread.Docx(docx_factory.runbook(steps=2, parts=2, actions=3))
    blocks = d.blocks()
    pats = {p["sig"]: p for p in detect.find_patterns(blocks)}
    assert pats["P|style:heading 1"]["count"] == 2
    assert pats["T|section:PART|2B7DE9"]["count"] == 4
    assert pats["T|action"]["count"] == 12
    assert pats["T|marker|16233D"]["count"] == 12
    assert pats["T|command|1E2A3A"]["count"] == 2              # command strip: a trap
    assert pats["T|phrase|2B5DA8"]["count"] == 2               # decision chip: a trap
    mapping, checkable = detect.suggest(list(pats.values()))
    assert mapping["P|style:heading 1"] == 1 and mapping["T|section:PART|2B7DE9"] == 2
    assert mapping["T|action"] == 3 and mapping["T|marker|16233D"] == 4 and checkable == 3
    assert mapping["T|command|1E2A3A"] == 0 and mapping["T|phrase|2B5DA8"] == 0
    assert mapping["P|style:heading 2"] == 0                   # front matter before the first step
    nodes, _ = tree.build(blocks, mapping)
    assert tree.counts(nodes) == {1: 2, 2: 4, 3: 12, 4: 12}
    idx = tree.by_id(nodes)
    n = next(x for x in nodes if x["code"] == "2.4")
    assert [x["short"] for x in tree.path_of(n, idx)] == ["Step 2", "Part 2", "2.4"]


def test_template_match_score():
    d1 = detect.find_patterns(docxread.Docx(docx_factory.runbook()).blocks())
    mapping, _ = detect.suggest(d1)
    d2 = detect.find_patterns(docxread.Docx(docx_factory.runbook(steps=3, extra_title=" (new)")).blocks())
    assert detect.match_score(d2, mapping) >= 90          # only a new emphasis pattern is unknown
    assert detect.match_score(d2, {"P|style:Title": 1}) < 20


def test_rendering_keeps_colours_code_lists_and_images():
    d = docxread.Docx(docx_factory.runbook(steps=1, parts=1, actions=1))
    blocks = d.blocks()
    for b in blocks:
        b["sig"] = detect.signature(b)[0]
    sigs = {b["i"]: b["sig"] for b in blocks if b.get("sig")}
    stored = []
    r = render.Renderer(d, lambda p, data: stored.append(p) or "/media/rb/x/" + p.split("/")[-1], {}, sigs)
    out = r.render(0, len(blocks))
    assert '<div class="cmd"><code>git status</code>' in out                     # command strip
    assert 'background:#2B5DA8">REJECT H₀</b>' in out                             # decision chip as a band
    assert 'data-copy>Copy</button><code>SELECT *\nFROM orders;</code></div>' in out      # code block, copy button
    assert '<div class="code" style="background:#F7F9FC' in out                            # light, as in Word
    assert '<ol start="1"><li>Item one</li><li>Item two</li></ol>' in out
    assert 'class="label-p"' in out and '<img src="/media/rb/x/image1.png"' in out
    assert stored == ["word/media/image1.png"]
    assert "<script" not in render.Renderer(d, lambda p, x: "", {}, {}).render(0, len(blocks)).lower()


# ------------------------------------------------------------------ the whole flow through the pages

def test_upload_map_preview_publish_and_navigate(boss):
    ids = setup_org()
    r = boss.post("/admin/runbooks", {"name": "Sample", "idem": "rb1"})
    rid = r["headers"]["location"].split("/")[-1]
    boundary = "----skillops"
    data = docx_factory.runbook()
    raw = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"csrf\"\r\n\r\n{boss.csrf}\r\n"
           f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"sample.docx\"\r\n"
           "Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    r = boss.request("POST", f"/admin/runbooks/{rid}/upload", raw=raw, content_type=f"multipart/form-data; boundary={boundary}")
    assert r["statusCode"] == 303, body(r)[:300]
    vid = service.list_versions(rid)[0]["id"]
    page = boss.get(f"/admin/runbooks/{rid}/v/{vid}/map")
    assert page["statusCode"] == 200 and "Band: action word + ID" in body(page)
    form = {f"lvl:{p['sig']}": str(service.get_version(vid)["mapping"][p["sig"]])
            for p in service.get_version(vid)["patterns"]}
    form.update({"checkable": "3", "name1": "Step", "name2": "Part", "name3": "Action", "name4": "Sub-step",
                 "save_template": "1", "template_name": "Robomy runbook v3"})
    r = boss.post(f"/admin/runbooks/{rid}/v/{vid}/map", form)
    assert r["statusCode"] == 303
    assert [t["name"] for t in service.list_templates()] == ["Robomy runbook v3"]
    v, blocks, nodes = service.preview(vid)
    first_action = next(n for n in nodes if n["level"] == 3)
    boss.post(f"/admin/runbooks/{rid}/v/{vid}/edit", {"node": first_action["id"], "action": "rename", "value": "Renamed action"})
    assert boss.get(f"/admin/runbooks/{rid}/v/{vid}/preview?node={first_action['id']}")["statusCode"] == 200
    r = boss.post(f"/admin/runbooks/{rid}/v/{vid}/publish", {"idem": "pub1"})
    assert service.get_version(vid)["status"] == "published"
    # use it for the Airtel project of the DBI programme
    airtel = next(j for j in org.list_projects(ids["p1"]) if j["name"] == "Airtel")
    boss.post(f"/admin/programmes/{ids['p1']}/projects/{airtel['id']}/runbook", {"runbook_id": rid})
    # a trainer opens it: "continue" goes to the first open item
    t = Browser()
    t.sign_in_local("trainer1@robomy.test")
    r = t.get(f"/b/{ids['A']}/p/{airtel['id']}")
    assert r["statusCode"] == 303 and r["headers"]["location"].endswith("/teach")      # continue = class view
    r = t.get(r["headers"]["location"])
    assert r["statusCode"] == 303 and "/teach/" in r["headers"]["location"] and "#a-" in r["headers"]["location"]
    first_item = r["headers"]["location"].split("#a-")[1]
    cls = t.get(r["headers"]["location"].split("#")[0])
    assert cls["statusCode"] == 200 and "Renamed action" in body(cls) and "data-columns" in body(cls)
    r = {"headers": {"location": f"/b/{ids['A']}/p/{airtel['id']}/i/{first_item}"}}
    page = t.get(r["headers"]["location"])
    assert page["statusCode"] == 200 and "Renamed action" in body(page) and "Mark complete" in body(page)
    assert t.get(r["headers"]["location"] + "?present=1")["statusCode"] == 200
    s = t.get(f"/b/{ids['A']}/p/{airtel['id']}/search?q=needle1.2")
    assert "needle1.2" in body(s) and "1.2" in body(s)
    # tick with the page's script (JSON) and by plain form
    item = r["headers"]["location"]
    j = t.post(item + "/tick", {}, headers={"accept": "application/json", "x-requested-with": "fetch"})
    assert j["statusCode"] == 200 and json.loads(j["body"])["state"] == "done"
    t.get(item)
    assert t.post(item + "/tick", {})["statusCode"] == 303           # second tick: same single tick
    batch = org.get_batch(ids["A"])
    vid_a = progress.batch_version(batch, org.get_project(ids["p1"], airtel["id"]))
    assert len(progress.ticks(ids["A"], vid_a)) == 1
    # trainer 2 cannot open batch A's runbook
    t2 = Browser()
    t2.sign_in_local("trainer2@robomy.test")
    assert t2.get(item)["statusCode"] == 403
    # the supervisor's dashboard shows the position from the stored tick
    d = boss.get("/dashboard?programme=all&institution=all")
    assert "Step 1 · Part 1 · 1.1" in body(d)


def test_ticks_are_once_only_and_per_batch(aws):
    ids = setup_org()
    rid, vid = publish_runbook(docx_factory.runbook())
    pub = service.published(vid)
    item = pub["checkables"][0]["id"]
    t1 = Staff("trainer1@robomy.test", "trainer", "Trainer 1", {ids["A"]})
    t3 = Staff("trainer3@robomy.test", "trainer", "Trainer 3", {ids["C"]})
    results = []
    threads = [threading.Thread(target=lambda: results.append(progress.tick(ids["A"], vid, item, t1)[1])) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert results.count(True) == 1 and len(progress.ticks(ids["A"], vid)) == 1
    assert progress.ticks(ids["C"], vid) == {}                       # batch C keeps its own progress
    progress.tick(ids["C"], vid, item, t3)
    with pytest.raises(Exception) as e:
        progress.untick(ids["A"], vid, item, t3)                     # not their tick
    assert "marked this item" in str(e.value)
    assert progress.untick(ids["A"], vid, item, t1) is True
    assert progress.untick(ids["A"], vid, item, t1) is False         # repeating is harmless
    ev = progress.events(ids["A"])
    assert [e["action"] for e in ev] == ["untick", "tick"]


def test_summary_counts_come_from_ticks(aws):
    ids = setup_org()
    rid, vid = publish_runbook(docx_factory.runbook(steps=2, parts=2, actions=3))
    pub = service.published(vid)
    t1 = Staff("trainer1@robomy.test", "trainer", "Trainer 1", {ids["A"]})
    chosen = [n for n in pub["checkables"] if n["code"] in ("1.1", "1.2", "1.5")]
    for n in chosen:
        progress.tick(ids["A"], vid, n["id"], t1)
    s = progress.summary(ids["A"], vid)
    assert (s["done"], s["total"]) == (3, 12)
    assert s["position"] == "Step 1 · Part 2 · 1.5"
    assert [n["code"] for n in s["skipped"]] == ["1.3", "1.4"]
    step1 = next(n for n in pub["nodes"] if n["level"] == 1)
    assert s["per"][step1["id"]] == [3, 6]


def test_new_version_carries_ticks_and_lists_unmatched(aws):
    ids = setup_org()
    rid, v1 = publish_runbook(docx_factory.runbook())
    airtel = next(j for j in org.list_projects(ids["p1"]) if j["name"] == "Airtel")
    org.set_project_runbook(ids["p1"], airtel["id"], rid)
    airtel = org.get_project(ids["p1"], airtel["id"])
    batch = org.get_batch(ids["A"])
    assert progress.batch_version(batch, airtel) == v1               # pinned on first use
    t1 = Staff("trainer1@robomy.test", "trainer", "Trainer 1", {ids["A"]})
    pub1 = service.published(v1)
    for n in pub1["checkables"][:4]:
        progress.tick(ids["A"], v1, n["id"], t1)
    # version 2: action 1.2 removed, everything else the same
    v2 = service.add_version(rid, "v2.docx", docx_factory.runbook(drop={"1.2"}), "boss")
    assert service.get_version(v2)["status"] == "mapping"
    assert service.get_version(v2)["counts"] == {"1": 2, "2": 4, "3": 11, "4": 11}   # the previous mapping was reused
    service.publish(v2, "boss")
    assert progress.batch_version(org.get_batch(ids["A"]), airtel) == v1  # stays until moved
    _, unmatched = service.match_items(pub1, service.published(v2))
    assert [n["code"] for n in unmatched] == ["1.2"]
    boss = Staff("boss@robomy.test", "superadmin", "Boss")
    result = progress.move_batch(org.get_batch(ids["A"]), airtel, v2, boss)
    assert result["copied"] == 3 and len(result["unmatched"]) == 1
    assert len(progress.ticks(ids["A"], v1)) == 4                    # old ticks kept
    assert {service.published(v2)["index"][i]["code"] for i in progress.ticks(ids["A"], v2)} == {"1.1", "1.3", "1.4"}


# ------------------------------------------------------------------ acceptance test with the sample runbook (section 5.5)

@pytest.mark.skipif(not os.path.exists(AIRTEL), reason="the sample Airtel runbook is not on this computer")
def test_airtel_acceptance(aws):
    with open(AIRTEL, "rb") as f:
        data = f.read()
    d = docxread.Docx(data)
    blocks = d.blocks()
    assert len(blocks) == 4994 + 1542
    pats = detect.find_patterns(blocks)
    mapping, checkable = detect.suggest(pats)
    nodes, _ = tree.build(blocks, mapping)
    counts = tree.counts(nodes)
    assert counts[1] == 10 and counts[2] == 34 and counts[3] == 418 and checkable == 3
    by_sig = {p["sig"]: p for p in pats}
    assert {w: x["count"] for w, x in by_sig["T|action"]["words"].items()} == {
        "DO": 205, "SAY": 122, "SHOW": 32, "CHECK": 24, "WATCH": 14, "ASK": 8, "COMMIT": 8, "HOOK": 5}
    # traps are content, never headings
    heading_blocks = {n["start"] for n in nodes}
    for b in blocks:
        if b.get("sig") and (b["sig"].startswith("T|command") or b["sig"].startswith("T|phrase")):
            assert b["i"] not in heading_blocks
    idx = tree.by_id(nodes)
    n24 = next(n for n in nodes if n["code"] == "2.4")
    assert [x["short"] for x in tree.path_of(n24, idx)][:2] == ["Step 2", "Part 1"]
    # five actions of different kinds render with their own badge colours and content
    rid, vid = publish_runbook(data, "Airtel")
    pub = service.published(vid)
    for word, fill in (("SAY", "2B7DE9"), ("DO", "16233D"), ("SHOW", "0E7C74"), ("CHECK", "2C7A4B"), ("COMMIT", "A02C2C")):
        n = next(x for x in pub["checkables"] if x["label"] == word)
        assert n["fill"] == fill
        html = service.item_html(vid, n["id"])
        assert len(html) > 200, word
    do_with_code = [x for x in pub["checkables"] if x["label"] == "DO" and 'class="code"' in service.item_html(vid, x["id"])]
    assert do_with_code
    assert re.search(r'<img src="/media/rb/', "".join(service.item_html(vid, x["id"]) for x in pub["checkables"][:80]))
