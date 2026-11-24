"""Source witnesses and editable, occurrence-local readings of formula structure."""
import hashlib
import json
from pathlib import Path
import re

import manual_review as manual

KINDS = ("relation", "destination")
FIELDS = {"relation": "logical_relations", "destination": "resolved_references"}
REF = re.compile(r"\\(?:eqref|ref|autoref|[cC]ref)\*?\s*\{([^{}]+)\}")
DISPLAY = re.compile(r"\\begin\s*\{(align(?:at)?|flalign|gather|eqnarray|IEEEeqnarray|equation|multline)(\*?)\}")
GROUP = re.compile(r"\\begin\s*\{subequations\}")


def evidence_path(directory):
    return Path(directory) / "source_evidence.json"


def hash_text(text):
    return hashlib.sha256(text.encode("utf8")).hexdigest()


def source_evidence(selected, sources, paper, normalized):
    import latex2nl as L
    view = L.lexical_view(selected)
    for start, end in reversed(L.definition_spans(view)):
        view = view[:start] + " "*(end-start) + view[end:]
    anchors, groups, incomplete_groups = {}, {}, []
    for match in DISPLAY.finditer(view):
        env = match[1] + match[2]
        closing = L.find_env_end(view, match.end(), env)
        if not closing:
            continue
        body = view[match.end():closing.start()]
        rows = L.alignment_cells(body)
        for index, cells in enumerate(rows, 1):
            for _, _, kind, label in L.display_marks("&".join(cells)):
                if kind != "label":
                    continue
                witness = {"env": env, "offset": match.start(), "end": closing.end()}
                if match[1] in L.ROW_NUMBERED_ENVS and len(rows) > 1 and r"\intertext" not in body:
                    witness["row"] = index
                anchors[label] = witness
    for match in GROUP.finditer(view):
        closing = L.find_env_end(view, match.end(), "subequations")
        if closing:
            labels = [key for _, _, kind, key in L.display_marks(view[match.end():closing.start()]) if kind=="label"]
            members = list(DISPLAY.finditer(view,match.end(),closing.start()))
            covered = {v["offset"] for v in anchors.values()}
            incomplete = not members or any(m.start() not in covered for m in members)
            for m in members:
                if m[1] in L.ROW_NUMBERED_ENVS:
                    end = L.find_env_end(view,m.end(),m[1]+m[2])
                    if end and any(not any(k=="label" for _,_,k,_ in L.display_marks("&".join(row)))
                                   for row in L.alignment_cells(view[m.end():end.start()])):
                        incomplete = True
            for key in labels:
                if key not in anchors:
                    if incomplete:
                        incomplete_groups.append(key)
                    groups[key] = list(dict.fromkeys(k for k in labels if k in anchors))
    return dict(paper=paper, selected=selected, files=sources, labels=anchors, groups=groups,
                incomplete_groups=incomplete_groups, normalized_sha256=hash_text(normalized))


def read_json(path, default):
    return json.loads(path.read_text(encoding="utf8")) if path.is_file() else default


def evidence(directory, paper):
    out = Path(directory)
    doc = read_json(evidence_path(out), {})
    if doc.get("paper") != paper:
        return {}, {}
    execution = read_json(out/"execution.json", {})
    numbers = execution.get("label_numbers", {}) if execution.get("status") in ("observed","partial") else {}
    return doc, numbers


def logical_relations(formula):
    """Join additive continuation lines only when there is one unambiguous side."""
    import latex2nl as L
    rows = formula.get("rows", [])
    if not rows:
        top = formula.get("extracted_top_relation", formula.get("top_relation"))
        if top:
            return [dict(top)]
        # The printed operator is available even when a decorated arrow has
        # no entry in the mathematical role table. Keep its complete spelling.
        body = formula.get("expanded", "")
        for pos, end, kind in L._scan_depth(body):
            if kind not in ("stackrel", "overset", "underset"):
                continue
            cursor = end
            for _ in range(2):
                while cursor < len(body) and body[cursor].isspace():
                    cursor += 1
                if not body.startswith("{",cursor):
                    break
                close = L.find_group_end(body,cursor)
                if close is None:
                    break
                base = body[cursor+1:close-1].strip()
                cursor = close
            else:
                if ((base in ("=", "<", ">") or base.lstrip("\\") in L.REL_COMMANDS or
                        re.fullmatch(r"\\[A-Za-z]*(?:arrow|mapsto)", base)) and
                        body[:pos].strip() and body[cursor:].strip()):
                    return [dict(lhs=body[:pos].strip(), symbol=body[pos:cursor], rhs=body[cursor:].strip())]
        return []
    result = []
    for n, row in enumerate(rows, 1):
        relations = row.get("relations", [])
        text = "".join(L.alignment_cells(row.get("expanded", ""))[0]).strip()
        if (not relations and re.match(r"^[+-]", text) and result and "rhs" in result[-1]
                and len(rows[n-2].get("relations", [])) <= 1 and result[-1]["rows"][-1] == n-1):
            result[-1]["rhs"] += " " + text
            result[-1]["rows"].append(n)
        elif relations:
            for relation in relations:
                value = dict(relation, rows=[n])
                if (len(relations)==1 and result and "expression" in result[-1] and
                        re.match(r"^[+-]", value["lhs"])):
                    previous = result.pop()
                    value["lhs"] = previous["expression"] + " " + value["lhs"]
                    value["rows"] = previous["rows"] + [n]
                result.append(value)
        elif text:
            result.append({"expression":text,"rows":[n]})
    return result


def candidates(formulas, doc, numbers):
    import latex2nl as L
    targets = L.formula_destinations(formulas)
    labels, groups = doc.get("labels", {}), doc.get("groups", {})
    by_id = {f["id"]: f for f in formulas}
    for f in formulas:
        raw, fid = f.get("raw",""), f["id"]
        reasons = []
        if f.get("rows") and not f.get("opaque"):
            reasons.append("核对对齐列、换行及续写的操作数")
        if re.search(r"\\(?:not(?![A-Za-z])|overset|underset|stackrel)\b", raw) and not f.get("opaque"):
            reasons.append("核对修饰后的关系符号")
        if reasons or f.get("extracted_top_relation", f.get("top_relation")):
            yield f, "relation", logical_relations(f), "；".join(reasons) or "核对公式关系和操作数", {}
        keys = []
        if f.get("ref_kind") == "ref":
            key = f.get("ref_target", "")
            keys = [key] if key in targets or key in groups else L.reference_keys(key)
        elif f.get("kind") != "ref":
            for ref in REF.finditer(raw):
                keys.extend([ref[1]] if ref[1] in targets else L.reference_keys(ref[1]))
        keys = list(dict.fromkeys(k for k in keys if k in targets or k in labels or k in groups))
        if not keys:
            continue
        values, observed = [], {}
        for key in keys:
            members = groups.get(key, [key])
            for child in members:
                if child in targets:
                    value = dict(label=key, **targets[child])
                    if value not in values:
                        values.append(value)
            if key in numbers:
                observed[key] = numbers[key]
        # The key covers the destination content, so an old decision cannot
        # silently follow a renumbered formula after the normalization changes.
        context = {v["formula"]:by_id[v["formula"]].get("raw") for v in values}
        yield f, "destination", values, "核对引用键、目标公式和编号行", dict(
            labels=keys, tex_numbers=observed, target_formulas=context,
            incomplete=any(k in doc.get("incomplete_groups", []) for k in keys))


def target_key(f, kind, detail):
    return manual.digest([kind, f["id"], f.get("raw"), f.get("context_before"),
                          f.get("context_after"), detail.get("labels"), detail.get("target_formulas")])


def projected_relation(value):
    if len(value)==1 and "symbol" in value[0]:
        return {k:value[0][k] for k in ("symbol","lhs","rhs")}
    return None


def attach(formulas, doc, numbers, decisions):
    for f, kind, proposal, why, detail in candidates(formulas, doc, numbers):
        key = target_key(f, kind, detail)
        decision = decisions.get((kind,key))
        value = decision["value"] if decision else proposal
        if kind == "relation":
            f["extracted_top_relation"] = f.get("top_relation")
            f["top_relation"] = projected_relation(value)
        else:
            f["extracted_ref_destinations"] = f.get("ref_destinations", [])
            f["ref_destinations"] = value
        f[FIELDS[kind]] = value
        f.setdefault("structure_reviews", {})[kind] = dict(anchor=key, proposal=proposal,
            source=decision.get("source", "manual") if decision else "reference", reason=why, **detail)


def validate(kind, value, formulas, f, detail):
    if not isinstance(value, list) or len(value)>2000:
        raise ValueError("需要记录列表。")
    by_id = {r["id"]:r for r in formulas}
    for row in value:
        if not isinstance(row,dict):
            raise ValueError("每一项应为一条记录。")
        if kind=="relation":
            if set(row)-{"symbol","lhs","rhs","expression","rows"}:
                raise ValueError("关系字段无法识别。")
            fields = ("expression",) if "expression" in row else ("lhs","symbol","rhs")
            if "expression" in row and any(k in row for k in ("lhs","symbol","rhs")):
                raise ValueError("同一项只能表示独立表达式或关系。")
            if not all(isinstance(row.get(k),str) and len(row[k]) <= 65536 for k in fields):
                raise ValueError("请填写关系的左右侧和符号，或一个完整表达式。")
            if not row.get("expression", row.get("symbol", "")).strip():
                raise ValueError("独立表达式或关系符不能为空。")
            if "rows" in row:
                nums = row["rows"]
                if (not isinstance(nums,list) or not nums or not all(type(n) is int and 1<=n<=len(f.get("rows",[])) for n in nums)
                        or nums != sorted(set(nums))):
                    raise ValueError("请输入原公式中有效、递增的行号。")
        else:
            target = by_id.get(row.get("formula")) if isinstance(row.get("formula"),str) else None
            if (set(row)-{"label","formula","row"} or row.get("label") not in detail["labels"]
                    or not target or target.get("kind") not in ("inline","display")):
                raise ValueError("请使用本条引用键和稿件内有效的公式 ID。")
            if "row" in row and (type(row["row"]) is not int or not 1 <= row["row"] <= len(target.get("rows",[]))):
                raise ValueError("目标行号超出公式范围。")
    if kind=="destination" and set(detail["labels"])!={v["label"] for v in value}:
        raise ValueError("每个引用键都需要目标。")


def verify(out, formulas, normalized, paper, decisions, blocks):
    import latex2nl as L
    directory = Path(out)
    doc, numbers = evidence(out, paper)
    errors = []
    stats = read_json(directory/"normalize-stats.json", {})
    if stats.get("source_evidence") and (not doc or doc.get("normalized_sha256") != hash_text(normalized)
            or stats.get("source_evidence_sha256") != manual.digest(doc)):
        errors.append("source evidence is missing or belongs to another normalization")
    # Match the original outer row independently of the flattened math parser.
    targets = L.formula_destinations(formulas)
    retained = [block.get("raw", "") for block in blocks]
    for key, witness in doc.get("labels", {}).items():
        if key not in numbers:
            continue
        target = targets.get(key)
        if target is None:
            if not any(r"\label{"+key+"}" in raw for raw in retained):
                errors.append("compiled formula label missing: " + key)
        elif "row" in witness and target.get("row") != witness["row"]:
            errors.append("source alignment row disagrees for " + key)
    execution = read_json(directory/"execution.json", {})
    if execution.get("status") == "observed":
        literals = [re.sub(r"\s+", "", f.get("raw","")) for f in formulas if f.get("kind") == "inline"]
        cursor = 0
        for site in execution.get("visible_inline", []):
            raw = site["latex"]
            try:
                cursor = literals.index(re.sub(r"\s+", "", raw), cursor) + 1
            except ValueError:
                errors.append(f'{site["file"]}@{site["offset"]}: compiled inline formula missing or out of order: {raw}')
    pending = []
    expected = set()
    for f, kind, proposal, why, detail in candidates(formulas, doc, numbers):
        expected.add((f["id"],kind))
        anchor = target_key(f, kind, detail)
        record = f.get("structure_reviews", {}).get(kind, {})
        decision = decisions.get((kind,anchor))
        value = decision["value"] if decision else proposal
        source = decision.get("source", "manual") if decision else "reference"
        if (record != dict(anchor=anchor, proposal=proposal, source=source, reason=why, **detail)
                or f.get(FIELDS[kind]) != value or
                (f.get("top_relation") != projected_relation(value) if kind=="relation"
                 else f.get("ref_destinations") != value)):
            errors.append("review value or provenance differs at " + f["id"] + "/" + kind)
        if not decision and (not proposal or detail.get("incomplete") or kind=="destination" and
                set(detail["labels"]) != {v["label"] for v in proposal}):
            pending.append(f["id"] + "/" + kind)
        if decision:
            validate(kind, value, formulas, f, detail)
            if kind == "destination":
                for key in detail["tex_numbers"]:
                    observed = [v for v in proposal if v["label"] == key]
                    chosen = [v for v in value if v["label"] == key]
                    if observed and (any(v not in chosen for v in observed) or
                                     not detail.get("incomplete") and chosen != observed):
                        errors.append(source + " destination contradicts compiled source anchor: " + key)
    for f in formulas:
        for kind in KINDS:
            if ((kind in f.get("structure_reviews", {}) or FIELDS[kind] in f) and (f["id"],kind) not in expected):
                errors.append("unexpected structure review at " + f["id"])
    return errors, pending
