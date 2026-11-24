"""Collect editable questions and diagnostic evidence from one pipeline run."""
from pathlib import Path
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import latex2nl
import manual_review as M
import normalize
import split_audit
import pending_names
import review_labels
import formula_review
import rule_reviews
from lexicon import PAPER_EVIDENCE

GROUPS = {"relation":"公式关系与续行", "destination":"公式引用目标", "sentence": "正文句界", "display": "展示公式句界", "slot": "公式文字槽",
          "construction": "并置读法", "role": "公式角色", "names": "命令名称",
          "content": "保留内容", "conditional": "条件与执行", "human": "人工转交",
          "formula": "公式诊断", "check": "验收与警告", "unresolved": "其他未决项"}
SECTIONS = {"blank": "空白条目标签", "labeled": "条目标签确认"}
OPTIONS = {"sentence": ["split", "join"], "display": ["split", "join"],
           "slot": list(latex2nl.SLOT_ANSWERS), "construction": list(review_labels.CONSTRUCTIONS),
           "role": list(review_labels.ROLES), "names": list(normalize.NAME_ACTIONS)}
DISPLAY_MESSAGES = ("a new sentence follows but the display ends ", "the sentence continues in lower case but the display ",
                    "display ends without punctuation and an inline formula follows;")


def read(out, filename, default=None):
    path = out / filename
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def rows(doc):
    return doc.get("items", []) if isinstance(doc, dict) else doc


def catalog(directory):
    out = Path(directory)
    stats = read(out, "normalize-stats.json")
    status = read(out, "split_status.json")
    lex = read(out, "paper_lexicon.json")
    roles = read(out, "roles.json")
    # Standalone splitter output also carries the hash in each slot question.
    slot_rows = rows(read(out, "slot_todo.json"))
    paper = (stats.get("paper_id") or status.get("paper_id") or lex.get("paper")
             or roles.get("paper") or next((s["paper"] for s in slot_rows if s.get("paper")), None))
    if not paper:
        # Older unversioned runs can be inspected and annotated without sharing a paper scope.
        normalized = out / "normalized.tex"
        if not normalized.exists():
            raise ValueError("缺少论文身份或 normalized.tex，请先运行切分。")
        from hashlib import sha256
        paper = sha256(normalized.read_bytes()).hexdigest()
    doc = read(out, "formulas.json", [])
    forms = {f["id"]: f for f in (doc.get("formulas", []) if isinstance(doc, dict) else doc)}
    sentences = {}
    ordered = []
    indices = {}
    source = out / "sentences.jsonl"
    if source.exists():
        for line in source.read_text(encoding="utf8").splitlines():
            if line.strip():
                sent = json.loads(line)
                ordered.append(sent["text"])
                for fid in sent.get("formulas", []):
                    indices[fid] = len(ordered) - 1
                    sentences[fid] = sent.get("text", "")
    items = {}

    def add(kind, target, title, location, context, *, level="todo", options=None, current="", detail=None, file=""):
        key = (kind, target)
        if key not in items:
            items[key] = dict(kind=kind, target=target, title=str(title), location=str(location or ""),
                              context=str(context or ""), options=options or OPTIONS.get(kind, ["ack"]),
                              current=current or "", level=level, detail=detail or {}, files=[])
        item = items[key]
        if file and file not in item["files"]:
            item["files"].append(file)
        return item

    evidence, numbers = formula_review.evidence(out, paper)
    def source_text(value):
        return re.sub(r"\bMATH\d+\b", lambda m: forms.get(m[0], {}).get("raw") or m[0], value or "")

    for formula, kind, proposal, why, extra in formula_review.candidates(list(forms.values()), evidence, numbers):
        anchor = formula_review.target_key(formula, kind, extra)
        details = dict(latex=formula["raw"], rows=[r.get("expanded", "") for r in formula.get("rows", [])],
                       proposal=proposal, **extra)
        for label in extra.get("labels", []):
            witness = evidence.get("labels", {}).get(label)
            if witness:
                details.setdefault("source_displays", {})[label] = evidence["selected"][witness["offset"]:witness["end"]]
        item = add(kind, anchor, why, formula["id"], formula.get("context_before","") + " " +
                   formula.get("context_after",""), options=["review"], detail=details, file="formulas.json")
        item["has_candidate"] = bool(proposal) and not extra.get("incomplete") and (kind != "destination" or
            set(extra["labels"]) == {v["label"] for v in proposal})

    for q in rows(read(out, "sentence_todo.json")):
        add("sentence", q["sig"], "这里是否另起一句？", q.get("sid_before") or q.get("unit"),
            q.get("before", "") + "  ¶  " + q.get("after", ""), current=q.get("answer") or q.get("default"),
            file="sentence_todo.json")

    slot_questions = {q["anchor"]: q for q in rows(read(out, "slot_questions.json")) if q.get("anchor")}
    slot_records = {s["anchor"]: s for f in forms.values() for s in f.get("text_slots", []) if s.get("anchor")}

    def slot(q, fid, file):
        if not q.get("anchor"):
            return add("formula", M.digest(q), q.get("raw", "旧格式文字槽"), fid, q.get("context"),
                       detail=q, level="warning", file=file)
        record = slot_records.get(q["anchor"], q)
        evidence = slot_questions.get(q["anchor"], q)
        current, suggestion = review_labels.label_values("slot", q, record)
        item = add("slot", q["anchor"], q.get("raw", "").strip() or q["id"], q["id"],
                   evidence.get("sentence") or source_text(sentences.get(fid)),
                   detail={"latex": evidence.get("latex") or forms.get(fid, {}).get("raw", ""),
                           "offset": evidence.get("offset"), "before": record.get("before"), "after": record.get("after")}, file=file)
        if record.get("rule") and current:
            item["rule"] = {"name": record["rule"], "answer": current}
        if current or suggestion or not item.get("suggestion"):
            item.update(current=current, suggestion=suggestion, label_source=record.get("source") or record.get("rule"))
        return item
    for q in slot_rows:
        slot(q, q["formula"], "slot_todo.json")
    for fid, form in forms.items():
        for record in form.get("text_slots", []):
            if record.get("anchor") and record.get("role") in OPTIONS["slot"]:
                slot(record, fid, "formulas.json")


    entries = lex.get("constructions", {})
    keys = M.construction_keys(entries)
    def construction(k, q, file):
        e = entries.get(k, {})
        example = next(iter(q.get("examples", [])), {})
        loc = e.get("location") or q.get("location") or k
        fid = loc.split("@")[0].split(".")[0]
        anchor = keys.get(k) or q.get("anchor")
        if not anchor:
            return add("check", M.digest(q), k, loc, q.get("question"), detail=q, level="warning", file=file)
        item = add("construction", anchor, e.get("shape") or k, loc, example.get("sentence") or source_text(sentences.get(fid)),
                   detail={"latex": example.get("formula") or forms.get(fid, {}).get("raw", ""),
                           "question": q.get("question", ""), "offset": e.get("offset"),
                           "scope_definition": e.get("scope_definition")},
                   current=e.get("reading"), file=file)
        current, suggestion = review_labels.label_values("construction", q, e)
        if current:
            item.update(current=current, label_source=e.get("source"))
            if (e.get("source") or "").partition(":")[0] in PAPER_EVIDENCE:
                item["rule"] = {"name": e["source"], "answer": current}
        if suggestion and not item.get("conflict"):
            # Different spellings can share one editable occurrence.
            hints = item.setdefault("suggestions", [])
            if suggestion not in hints:
                hints.append(suggestion)
            item["suggestion"] = hints[0]
        if e.get("conflict") or q.get("conflict"):
            item.update(conflict=e.get("conflict") or q["conflict"], current="", suggestion="", suggestions=[])
        return item
    for q in rows(read(out, "construction_todo.json")):
        construction(q["key"], q, "construction_todo.json")

    for k, entry in entries.items():
        if entry.get("reading") and keys.get(k):
            construction(k, {}, "paper_lexicon.json")

    def role(fid, row, file):
        item = add("role", M.role_key(fid, row), row.get("formula") or fid, fid, source_text(row.get("sentence")),
                   detail={"window": [source_text(s) for s in row.get("window", [])], "latex": forms.get(fid, {}).get("raw", "")}, current=row.get("final"), file=file)
        record = roles.get("roles", {}).get(fid, row)
        if record.get("source") == "rule" and record.get("final"):
            item["rule"] = {"name": record.get("rule") or "rule", "answer": record["final"]}
        current, suggestion = review_labels.label_values("role", row, record)
        item.update(current=current, suggestion=suggestion, label_source=record.get("source") if current else None)
        return item
    for q in rows(read(out, "role_todo.json")):
        role(q["fid"], q, "role_todo.json")
    for fid, record in roles.get("roles", {}).items():
        if record.get("anchor") and record.get("final") in OPTIONS["role"]:
            role(fid, dict(record["anchor"], final=record["final"], source=record.get("source")), "roles.json")
    trusted_names = normalize.NameTable()
    def name(key, row, file):
        if trusted_names.origin.get(key) in ("tex", "override"):
            return add("check", M.digest(["known_name", key]), "\\" + key, row.get("scope"),
                       "此命令已有 TeX 或更正表定义。", detail=row, level="warning", file=file)
        return add("names", key, "\\" + key, row.get("scope"), "\n".join(map(str, row.get("examples", []))),
                   current=row.get("action"), detail=row, file=file)
    for q in rows(read(out, "names_todo.json")):
        if q.get("paper") in (paper, None):
            name(q["name"], q, "names_todo.json")
    for key, sites in lex.get("unknown_commands", {}).items():
        fid = next(iter(sites), "").split(".")[0]
        name(key.lstrip("\\"), {"scope": fid, "examples": [forms.get(fid, {}).get("raw", "")]}, "paper_lexicon.json")
    for key in stats.get("kept_macros", {}):
        name(key.lstrip("\\"), {"scope": "保留宏", "examples":[stats["kept_macros"][key]]}, "normalize-stats.json")
    for key in stats.get("outside_vocabulary", {}):
        name(key.lstrip("\\"), {"scope":"命令清单"}, "normalize-stats.json")
    for key in stats.get("outside_math_vocabulary", {}):
        name(key.lstrip("\\"), {"scope": "正文"}, "normalize-stats.json")

    for filename in ("content_todo.json", "conditional_todo.json"):
        for q in rows(read(out, filename)):
            kind = "conditional" if q.get("kind") in ("conditional", "conditional_formula", "tex_execution") else "content"
            allowed = ["retain"] if q.get("kind") == "float" else ["ack"]
            add(kind, M.block_key(q), q.get("environment") or q.get("kind"), q.get("id"), q.get("raw"),
                detail={"reason": q.get("reason"), "anchor": q.get("anchor_sids", [])},
                options=allowed, file=filename)

    locations = {(e.get("location"), (e.get("model_review") or {}).get("rule")): k
                 for k,e in entries.items()}
    for warning in rows(read(out, "rule_reviews.json")):
        cat, loc = warning["kind"], warning["location"]
        item = None
        if cat == "role" and loc in roles.get("roles", {}):
            rec = roles["roles"][loc]
            item = role(loc, dict(rec.get("anchor", {}), final=rec.get("final")), "rule_reviews.json")
        elif cat == "slot":
            fid = loc.partition(".")[0]
            for s in forms.get(fid, {}).get("text_slots", []):
                if s.get("id") == loc:
                    item = slot(s, fid, "rule_reviews.json")
        elif cat == "construction" and (loc, warning.get("rule")) in locations:
            item = construction(locations[(loc, warning.get("rule"))], {}, "rule_reviews.json")
        if item is None:
            item = add("check", M.digest(warning), loc, loc, "", file="rule_reviews.json")
        item.update(current=warning.get("model_answer") if warning.get("applied_source") == "model"
                    else warning.get("rule_answer"), comparison=warning)
        if cat in ("slot", "role", "construction"):
            item["rule"] = {"name": warning["rule"], "answer": warning["rule_answer"]}
        if warning.get("status") != "confirmed":
            item["level"] = "warning"

    for filename in ("formula_diagnostics.json", "issues.json"):
        doc = read(out, filename)
        for q in doc.get("items", []) if filename.startswith("formula") else doc.get("downstream_diagnostics", []):
            add("formula", M.digest(q), q.get("kind"), q.get("slot") or q.get("formula"),
                forms.get(q.get("formula"), {}).get("raw"), detail=q, level="warning", file=filename)
    for q in rows(read(out, "human_todo.json")):
        underlying = q.get("detail", "").lstrip("\\")
        ref = next((i for i in items.values()
                    if (q.get("kind") == "role" and i["kind"] == "role" and i["location"] == underlying)
                    or (q.get("kind") == "name" and i["kind"] == "names" and i["target"] == underlying)), None)
        if ref and q.get("kind") in ("role","name"):
            ref["detail"]["referral"] = q.get("reason")
            if "human_todo.json" not in ref["files"]:
                ref["files"].append("human_todo.json")
        else:
            add("human", M.digest([q.get("reason"), q.get("detail"), q.get("source")]),
                q.get("reason"), q.get("source"), q.get("detail"), file="human_todo.json")
    log = out / "unresolved.log"
    for line in log.read_text(encoding="utf8").splitlines() if log.exists() else []:
        fid, _, msg = line.partition(": ")
        if fid in forms and msg.startswith(DISPLAY_MESSAGES):
            idx = indices.get(fid)
            nearby = "\n".join(ordered[max(0, idx-1):idx+2]) if idx is not None else ""
            add("display", M.digest([fid, forms[fid]["raw"], msg]), "展示公式后的句界", fid,
                nearby, detail={"latex": forms[fid]["raw"], "reason": msg},
                current="split" if msg.startswith("a new sentence") else "join", file="unresolved.log")
        elif line.strip():
            add("unresolved", M.digest(line), msg or line, fid, line, level="warning", file="unresolved.log")
    for filename in sorted({p.name for p in out.glob("issues*.json")}):
        doc = read(out, filename)
        for q in doc.get("entries", []):
            if q.get("gate") == "none" and q.get("route") == "ledger":
                continue
            add("check", M.digest(q), q.get("kind"), q.get("where"), q.get("site"),
                level="error" if q.get("gate") == "hard" else "warning", file=filename, detail=q)
        for warning in doc.get("pending", []):
            add("check", M.digest(warning), warning, "", "严格验收仍有此项待处理。", level="warning", file=filename)
    for q in lex.get("overridden_by_paper", []):
        add("check", M.digest(q), "原读法被论文声明覆盖", q.get("key"), q.get("why"),
            level="warning", detail=q, file="paper_lexicon.json")
    for group, info in roles.get("summary", {}).get("groups_with_conflicting_answers", {}).items():
        add("check", M.digest([group,info]), "同组公式角色有分歧", group, "",
            level="warning", detail={"answers":info}, file="roles.json")
    if (out / "split-audit.md").exists():
        audit_rows, audit_forms = split_audit.load_run(str(out))
        for idx, row in enumerate(audit_rows):
            text = row.get("text", "").rstrip()
            if text and text[-1] not in split_audit.TERMINAL:
                why = split_audit.classify(row, audit_rows, idx, audit_forms)
                if why in ("cut-sentence", "source-missing-mark", "brace-leak", "other"):
                    add("check", M.digest([row["sid"], text, why]), why, row["sid"], text,
                        level="warning", file="split-audit.md")
    execution = read(out, "execution.json")
    if execution.get("status") in ("partial", "pending", "failed"):
        reason = execution.get("reason") or execution["status"]
        add("conditional", M.digest(["execution",reason]), "TeX 执行记录", execution.get("stage"), reason,
            detail={"pending_sites": execution.get("pending_sites", [])}, file="execution.json", level="warning")
    # Future queue types remain visible and can be annotated without inventing an answer format.
    for path in out.glob("*_todo.json"):
        if path.stem[:-5] not in ("role","slot","construction","sentence","content","conditional","names","human"):
            for q in rows(read(out, path.name)):
                add("human", M.digest([path.name,q]), path.stem, "", "", detail=q, file=path.name)

    saved = M.records(out, paper)
    for key, (version, payload) in saved.items():
        if payload and key not in items and key[0] not in formula_review.KINDS:
            items[key] = payload["item"]
        if key in items:
            item = items[key]
            item["version"] = version
            if payload:
                item["decision"] = payload["decision"]
                comparison = item["decision"].get("comparison")
                if comparison:
                    item["comparison"] = comparison
                    if comparison["status"] == "disagreement":
                        item["level"] = "warning"
                else:
                    item.pop("comparison", None)
    for item in items.values():
        item.setdefault("version", 0)
        kind = item["kind"]
        item["model_eligible"] = (not item.get("decision") and item["options"] != ["ack"]
                                  and item.get("label_source") != "manual")
        decision = item.get("decision", {}).get("answer")
        item["required"] = item["level"] in ("todo", "error")
        if kind in ("role", "slot", "construction"):
            labeled = decision or (not item.get("conflict") and (item.get("current") or item.get("suggestion")))
            item["required"] = not labeled
        elif kind in formula_review.KINDS:
            item["required"] = not (decision or item["has_candidate"])
        elif decision and decision != "ack":
            item["required"] = False
        elif kind == "names" and "names_todo.json" not in item["files"]:
            # An inventory of names inside intact math is diagnostic.
            if item["target"].lstrip("\\\\") not in {s.lstrip("\\\\") for s in stats.get("outside_math_vocabulary", {})}:
                item.update(required=False, level="warning")
        if kind == "check" and (item.get("comparison") or {}).get("status") == "confirmed":
            item["required"] = False
        if not item["required"] and item["level"] == "todo":
            item["level"] = "optional"
    return dict(paper=paper, name=Path(stats.get("input") or out.name).name, directory=str(out),
                groups=GROUPS, sections=SECTIONS, classes=list(normalize.NAME_CLASSES), items=list(items.values()))


def change(directory, request, *, model=None):
    state = catalog(directory)
    if request.get("paper") != state["paper"]:
        raise M.Changed("稿件版本已变更，请刷新。")
    item = next((i for i in state["items"] if (i["kind"],i["target"]) ==
                 (request.get("kind"), request.get("target"))), None)
    if item is None:
        raise M.Changed("待办已变化，请刷新后重新选择。")
    if request.get("version") != item["version"]:
        raise M.Changed("此条目已更新，请刷新。")
    payload = None
    if request.get("op") == "save":
        answer = request.get("answer")
        if answer not in item["options"]:
            raise ValueError("请选择此项允许的答案。")
        note = request.get("note", "")
        if not isinstance(note, str) or len(note) > 4000:
            raise ValueError("审核备注最多 4000 字。")
        decision = {"source": "manual", "answer": answer, "note": note}
        if model is not None:
            if not item["model_eligible"]:
                raise M.Changed("此条目已有决定或需要人工处理。")
            decision.update(source="model", model=model)
            rule = item.get("rule")
            if rule:
                decision["comparison"] = dict(rule_reviews.compare(rule["name"], rule["answer"], answer, model),
                                              applied_source="model")
        if item["kind"] in formula_review.KINDS:
            forms = read(Path(directory), "formulas.json", [])
            formula = next(f for f in forms if f["id"] == item["location"])
            value = request.get("value")
            formula_review.validate(item["kind"], value, forms, formula, item["detail"])
            decision["value"] = value
        if item["kind"] == "names":
            decision["action"] = answer
            if answer in ("allow","font"):
                cls = "font" if answer == "font" else request.get("class")
                if cls not in normalize.NAME_CLASSES or ((cls == "font") != (answer == "font")):
                    raise ValueError("请选择有效的命令类别。")
                decision["class"] = cls
            if answer == "expand":
                body = request.get("body", "")
                if not isinstance(body, str) or not pending_names._plausible_body(body)[0] or re.search(
                        r"\\" + re.escape(item["target"]) + r"(?![A-Za-z@])", body):
                    raise ValueError("展开体为空、括号不匹配、含自引用或不符合名称表要求。")
                decision["body"] = body
        snapshot = {k:v for k,v in item.items() if k not in ("version","decision","group","subgroup","required")}
        payload = {"decision":decision, "item":snapshot}
    elif request.get("op") != "undo" or "decision" not in item:
        raise ValueError("没有可以撤销的审核决定。")
    M.save(directory, state["paper"], item["kind"], item["target"], item["version"], payload)
    return payload["decision"] if payload else None
