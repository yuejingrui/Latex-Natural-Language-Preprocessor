#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Names the pipeline cannot settle, and the answers that settle them.

A command name that survives normalisation is settled by whoever can settle it:

* TeX knows it -- ``scripts/tex_oracle.py`` asks the installed TeX and writes
  ``data/tex_names.json``; ``normalize.py`` then keeps, unwraps or drops it.
* The paper defines it -- the macro table expands it.
* Nothing knows it.  Those are exported here as questions, each with the shape
  of the call and a few real contexts, and the answers go to
  ``data/name_answers.json``, which ``normalize.py`` reads.

    # what is still open, with examples
    python3 pending_names.py --export pending.json --step 14 --limit 200

    # fill in "action" (and "class" or "body") for the items you can, then
    python3 pending_names.py --merge pending.json

    # how much is closed by TeX, by answers, and how much is left
    python3 pending_names.py --report --step 14

Endpoints are chosen by environment, so the hosted API and a local server are
both available without changing code:

    NL_PREP_BACKEND=api-openai|api-anthropic|local-openai   (default api-openai)
    NL_PREP_API_BASE        required: the OpenAI-compatible endpoint
    NL_PREP_ANTHROPIC_BASE  required: the Anthropic-compatible endpoint
    NL_PREP_API_KEY         the key for either API protocol
    NL_PREP_API_MODEL       default qwen3.7-flash
    NL_PREP_LOCAL_BASE      default http://127.0.0.1:11434/v1 (Ollama)
    NL_PREP_LOCAL_KEY       usually empty for a local server
    NL_PREP_LOCAL_MODEL     default qwen3:8b

    # what a backend is worth, measured on the names TeX already decided
    python3 pending_names.py --calibrate --backend api-openai --limit 40
    # then fill and merge
    python3 pending_names.py --answer-with-model pending.json
    python3 pending_names.py --merge pending.json
"""

from __future__ import annotations

import argparse
import collections
import json
import multiprocessing as mp
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import os
import sys as _sys
# pipeline/ is a directory inside the splitting package: the splitter's modules
# live one level up in ../scripts and the shared tables in ../data.  Both have to
# be importable whether this file runs from the package root or from its own
# directory, so the paths are put on sys.path before the local imports below.
_PKG_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(_PKG_ROOT, "scripts"),
           os.path.dirname(os.path.abspath(__file__))):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import latex2nl as L  # noqa: E402
import normalize as N  # noqa: E402

GTED = os.path.dirname(os.path.dirname(HERE))
#: written back by --merge; starts as a copy of the package's table
ANSWERS_PATH = os.path.join(os.path.dirname(HERE), "data", "name_answers.json")
if not os.path.exists(ANSWERS_PATH):
    ANSWERS_PATH = os.path.join(_PKG_ROOT, "data", "name_answers.json")

# --------------------------------------------------------------------------
# talking to a model
# --------------------------------------------------------------------------
#: One entry per backend.  ``(base var, key var, model var, protocol)``.  The
#: local deployment and the hosted API are both kept: either can answer, and the
#: answer file records which one did.
BACKENDS = {
    "api-openai": ("NL_PREP_API_BASE", "NL_PREP_API_KEY",
                   "NL_PREP_API_MODEL", "openai"),
    "api-anthropic": ("NL_PREP_ANTHROPIC_BASE", "NL_PREP_API_KEY",
                      "NL_PREP_API_MODEL", "anthropic"),
    "local-openai": ("NL_PREP_LOCAL_BASE", "NL_PREP_LOCAL_KEY",
                     "NL_PREP_LOCAL_MODEL", "openai"),
}
DEFAULT_BACKEND = "api-openai"

SYS_PROMPT = """You classify LaTeX command names for a pipeline that turns a \
mathematics paper into a sentence stream.  Each item gives a command name, how \
it is called, and real contexts from the paper.  Decide exactly one action:

- allow: it is a math symbol or an operator; keep it.  Give "class".
- font: it only selects a typeface for its argument (a font switch).  class is "font".
- unwrap: it takes one group that holds the content; keep the group, drop the name.
- ignore: it carries nothing of its own; drop the name.
- expand: it stands for something expressible in an existing command; give "body".
- unknown: the paper's own convention only; do not guess.

"class" must be one of: letter, bigop, binop, relation, open, close, separator, \
const, font, named_operator, fence, accent, text.  A single symbol used as a \
value or a variable is "const"; a blackboard or fraktur letter is "letter"; an \
integral, sum or product is "bigop"; an operator name such as Aut or Hom is \
"named_operator".

Answer with JSON only, no prose:
{"answers": [{"name": "...", "action": "...", "class": "...|null", \
"body": "...|null", "note": "one short reason"}]}
One entry per item, in the same order."""


_ENV_FILE_CACHE: dict = {}


def _env_file_values() -> dict:
    """Values from an env file, so a key need not sit in a shell or in history.

    Looked for, in order, first hit per name: ``$NL_PREP_ENV_FILE``,
    ``~/.config/nlprep/env``, ``./.env``.  An already-set environment variable
    always wins, so a one-off ``NL_PREP_API_KEY=... command`` still works.
    Format: one ``NAME=VALUE`` per line, ``#`` starts a comment, quotes and a
    leading ``export`` are tolerated (a shell rc file can be used as-is).
    """
    if _ENV_FILE_CACHE:
        return _ENV_FILE_CACHE
    out: dict = {}
    paths = []
    if os.environ.get("NL_PREP_ENV_FILE"):
        paths.append(os.path.expanduser(os.environ["NL_PREP_ENV_FILE"]))
    paths.append(os.path.expanduser("~/.config/nlprep/env"))
    paths.append(os.path.abspath(".env"))
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        for line in lines:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("export "):
                s = s[7:].lstrip()
            if "=" not in s:
                continue
            k, v = s.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k.startswith("NL_PREP_") and k not in out:
                out[k] = v
    _ENV_FILE_CACHE.update(out)
    return out


def _setting(name: str, default: str = "") -> str:
    """An environment variable, or the same name from an env file."""
    v = (os.environ.get(name) or "").strip()
    if v:
        return v
    return (_env_file_values().get(name) or "").strip() or default


def backend_config(name: str = "") -> dict:
    """Read one backend's settings from the environment.

    ``NL_PREP_BACKEND`` picks which, so the same code runs against the hosted
    API or a local server without a flag: set the variables and go.
    """
    name = name or _setting("NL_PREP_BACKEND") or DEFAULT_BACKEND
    if name not in BACKENDS:
        raise SystemExit("unknown backend %r; one of %s"
                         % (name, ", ".join(BACKENDS)))
    base_var, key_var, model_var, protocol = BACKENDS[name]
    base = _setting(base_var).rstrip("/")
    if not base and name == "api-openai":
        base = os.environ.get("NL_PREP_API_BASE", "").strip()
    if not base and name == "api-anthropic":
        base = os.environ.get("NL_PREP_ANTHROPIC_BASE", "").strip()

    if not base and name == "local-openai":
        base = "http://127.0.0.1:11434/v1"
    if not base:
        raise SystemExit("no endpoint configured: set NL_PREP_API_BASE "
                         "(OpenAI-compatible) or NL_PREP_ANTHROPIC_BASE, for example\n"
                         "  export NL_PREP_API_BASE=https://your-host/v1")
    model = _setting(model_var)
    if not model:
        model = "qwen3.7-flash" if name.startswith("api") else "qwen3:8b"
    return {
        "backend": name,
        "protocol": protocol,
        "base": base,
        "key": _setting(key_var),
        "model": model,
        "key_var": key_var,
        "base_var": base_var,
        "model_var": model_var,
    }


def _opener():
    """An opener that ignores the shell's proxy unless asked to use one.

    ``~/.bashrc`` commonly exports ``HTTP(S)_PROXY=http://127.0.0.1:7897`` for a
    Windows proxy.  When that proxy is not running, urllib sends every request to
    a dead socket and the pipeline reports ``Errno 110 Connection timed out`` --
    which looks like the API being down while a direct connection works.  The
    endpoint is reachable directly, so the proxy is ignored by default; set
    ``NL_PREP_PROXY`` to route through one on purpose.
    """
    proxy = os.environ.get("NL_PREP_PROXY")
    handler = (urllib.request.ProxyHandler({"http": proxy, "https": proxy})
               if proxy else urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(handler)


def _post(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with _opener().open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # the body says *why* the endpoint refused; without it a 400 is opaque
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:
            detail = ""
        raise RuntimeError("HTTP %s from %s: %s"
                           % (exc.code, url, detail)) from exc


def call_model(cfg: dict, user: str, timeout: int = 180,
               system: str = SYS_PROMPT) -> str:
    """Send one prompt, return the text of the reply.

    ``system`` lets another question kind reuse the same backend: the boundary
    asker sends its own instructions instead of the name-classification ones.
    """
    if cfg["protocol"] == "anthropic":
        url = cfg["base"] + "/v1/messages"
        headers = {"x-api-key": cfg["key"], "anthropic-version": "2023-06-01"}
        payload = {"model": cfg["model"], "max_tokens": 4000,
                   "temperature": 0, "system": system,
                   "messages": [{"role": "user", "content": user}]}
        out = _post(url, payload, headers, timeout)
        parts = out.get("content") or []
        return "".join(p.get("text", "") for p in parts
                       if isinstance(p, dict))
    url = cfg["base"] + "/chat/completions"
    headers = {}
    if cfg["key"]:
        headers["Authorization"] = "Bearer " + cfg["key"]
    payload = {"model": cfg["model"], "temperature": 0, "max_tokens": 4000,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}],
               "response_format": {"type": "json_object"}}
    out = _post(url, payload, headers, timeout)
    return (out.get("choices") or [{}])[0].get("message", {}).get("content", "")


def _json_from(text: str):
    """The JSON object in a reply, tolerating a fenced code block."""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except ValueError:
        pass
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except ValueError:
            return None
    return None


def make_user_prompt(items: list) -> str:
    lines = []
    for k, it in enumerate(items, 1):
        lines.append("%d. name: \\%s\n   called: %s" % (k, it["name"],
                                                       it.get("shape") or "?"))
        for ex in (it.get("examples") or [])[:3]:
            lines.append("   context: %s" % ex.replace("\n", " ")[:220])
    return "\n".join(lines)


def ask_batch(cfg: dict, items: list, timeout: int = 180) -> list:
    """Ask for one batch; return the answers that parse, keyed by name."""
    text = call_model(cfg, make_user_prompt(items), timeout=timeout)
    doc = _json_from(text)
    if not doc:
        return []
    got = doc.get("answers") if isinstance(doc, dict) else doc
    if not isinstance(got, list):
        return []
    by_name = {it["name"]: it for it in items}
    out = []
    for a in got:
        if not isinstance(a, dict):
            continue
        name = str(a.get("name") or "").strip().lstrip("\\")
        if name not in by_name:
            continue
        act = str(a.get("action") or "").strip()
        cls = a.get("class")
        cls = str(cls).strip() if isinstance(cls, str) else None
        out.append({"name": name, "action": act, "class": cls,
                    "body": a.get("body"), "note": a.get("note")})
    return out


def _plausible_body(body: str) -> tuple:
    """A macro body we are willing to paste into a paper.

    A model asked for an expansion sometimes writes the *shape* of one and
    leaves a placeholder: ``\\left\\{ \\begin{array}{} ... \\end{array} \\right.``
    was offered for ``\\Set``.  Pasting that would put three dots where the
    paper's set-builder notation was, so a body holding an ellipsis, an
    unbalanced brace, an ``@`` or a runaway length is refused.
    """
    if not body or not body.strip():
        return False, "empty"
    if "..." in body or "…" in body:
        return False, "contains an ellipsis placeholder"
    if body.count("{") != body.count("}"):
        return False, "unbalanced braces"
    if "@" in body:
        return False, "contains @"
    if len(body) > 160:
        return False, "longer than 160 characters"
    return True, ""


def _clean_body(rec: dict) -> tuple:
    """Check an ``expand`` record, else report why not."""
    if (rec or {}).get("action") != "expand":
        return True, ""
    return _plausible_body((rec.get("body") or "").strip())


def do_check_config(a) -> int:
    """Print what the backend resolves to, with the key masked."""
    from_files = _env_file_values()
    for name in ("api-openai", "api-anthropic", "local-openai"):
        try:
            cfg = backend_config(name)
        except SystemExit as exc:      # an endpoint nobody configured yet
            mark = "->" if name == (a.backend or _setting("NL_PREP_BACKEND")
                                    or DEFAULT_BACKEND) else "  "
            print("%s %-13s not configured: %s" % (mark, name, exc))
            continue
        key = cfg["key"]
        shown = ("%s…%s (%d chars)" % (key[:6], key[-4:], len(key))
                 if len(key) > 10 else ("(empty)" if not key else "(short)"))
        where = "from file" if cfg["key_var"] in from_files and \
            cfg["key_var"] not in os.environ else (
                "from shell" if os.environ.get(cfg["key_var"]) else "unset")
        mark = "->" if name == (a.backend or _setting("NL_PREP_BACKEND")
                                or DEFAULT_BACKEND) else "  "
        print("%s %-13s %-9s %s" % (mark, cfg["backend"], cfg["protocol"],
                                    cfg["base"]))
        print("     model=%s  %s=%s %s" % (cfg["model"], cfg["key_var"], shown,
                                           where))
    return 0


def do_answer(a) -> int:
    cfg = backend_config(a.backend)
    with open(a.answer_with_model, encoding="utf-8") as fh:
        doc = json.load(fh)
    items = [it for it in (doc.get("items") or [])
             if it.get("action") in (None, "", "unknown")]
    if a.limit:
        items = items[:a.limit]
    if not items:
        print("nothing to ask: every item already has an action")
        return 0
    print("backend %s (%s) model %s, %d item(s), batch %d"
          % (cfg["backend"], cfg["protocol"], cfg["model"], len(items),
             a.batch))
    if cfg["key"]:
        print("  key: set (%d chars, not printed)" % len(cfg["key"]))
    else:
        print("  note: %s is empty -- set it in the environment or in "
              "~/.config/nlprep/env" % cfg["key_var"])
    if a.dry_run:
        print("\n--- prompt for the first batch ---")
        print(make_user_prompt(items[:a.batch])[:1500])
        return 0
    by_name = {}
    asked = answered = rejected = failed = 0
    for i in range(0, len(items), a.batch):
        batch = items[i:i + a.batch]
        asked += len(batch)
        got = None
        for attempt in (1, 2):
            try:
                got = ask_batch(cfg, batch)
                break
            except (urllib.error.URLError, OSError, ValueError) as exc:
                if attempt == 2:
                    print("  batch %d failed twice: %r" % (i // a.batch, exc))
                else:
                    print("  batch %d failed, retrying: %r"
                          % (i // a.batch, exc))
        if got is None:
            failed += 1
            continue
        for ans in got:
            if ans["action"] not in N.NAME_ACTIONS:
                rejected += 1
                continue
            if ans["action"] in ("allow", "font") and (
                    ans["class"] not in N.NAME_CLASSES
                    or (ans["action"] == "font") != (ans["class"] == "font")):
                # do_merge refuses exactly these, so accepting them here only
                # moved the failure to the next command
                rejected += 1
                continue
            if ans["action"] == "expand":
                ok, why = _plausible_body((ans.get("body") or "").strip())
                if not ok:
                    print("  refused \\%s: expansion %s" % (ans["name"], why))
                    rejected += 1
                    continue
            by_name[ans["name"]] = ans
            answered += 1
        print("  batch %d/%d: %d answered so far"
              % (i // a.batch + 1, (len(items) + a.batch - 1) // a.batch,
                 len(by_name)))
    for it in doc["items"]:
        ans = by_name.get(it["name"])
        if not ans:
            continue
        it["action"] = ans["action"]
        it["class"] = ans["class"]
        it["body"] = ans["body"]
        it["note"] = ans["note"] or ""
        it["by"] = "model:%s:%s" % (cfg["backend"], cfg["model"])
    # write beside the file and rename: a Ctrl-C must not truncate the answers
    # that were just paid for
    tmp = a.answer_with_model + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, a.answer_with_model)
    print("asked=%d answered=%d rejected=%d failed_batches=%d; written back "
          "to %s" % (asked, answered, rejected, failed, a.answer_with_model))
    print("now run: pending_names.py --merge %s" % a.answer_with_model)
    return 1 if failed and not answered else 0


def do_calibrate(a) -> int:
    """Measure a backend against the names TeX already decided.

    ``data/tex_names.json`` is a labelled set: TeX states the class of a math
    symbol and whether a name is a font switch, has an argument, or carries
    nothing.  The model is asked the same question with the label hidden, so
    accuracy can be measured before any money is spent on the open names.
    """
    cfg = backend_config(a.backend)
    oracle = json.load(open(os.path.join(_PKG_ROOT, "data",
                                         "tex_names.json"),
                            encoding="utf-8"))["names"]
    truth = {n: r for n, r in oracle.items()
             if r.get("action") in N.NAME_ACTIONS}
    if not truth:
        print("no labelled names in tex_names.json; build it first")
        return 2
    P_EMPTY, P_STEP = True, a.step
    global EMPTY_TABLE
    global EMPTY_TABLE
    saved = EMPTY_TABLE
    EMPTY_TABLE = True
    try:
        agg = collect(a.step, a.corpus)
    finally:
        EMPTY_TABLE = saved
    pool = []
    for name, slot in agg.items():
        if name in truth and slot["examples"]:
            pool.append({"name": name,
                         "shape": (list(slot["shapes"]) or [""])[0],
                         "examples": slot["examples"]})
    pool.sort(key=lambda it: (-agg[it["name"]]["count"], it["name"]))
    # a spread over classes rather than only the frequent ones
    picked, seen_cls = [], collections.Counter()
    cap_cls = a.limit // 8 if a.limit else 40
    for it in pool:
        cls = truth[it["name"]].get("class") or truth[it["name"]]["action"]
        if seen_cls[cls] >= cap_cls:
            continue
        picked.append(it)
        seen_cls[cls] += 1
        if a.limit and len(picked) >= a.limit:
            break
    print("calibrating %s (%s) model %s on %d names TeX decided"
          % (cfg["backend"], cfg["protocol"], cfg["model"], len(picked)))
    if a.dry_run:
        print(make_user_prompt(picked[:a.batch])[:1200])
        return 0
    ok_action = ok_class = n = 0
    wrong = []
    for i in range(0, len(picked), a.batch):
        batch = picked[i:i + a.batch]
        try:
            got = ask_batch(cfg, batch)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print("  batch failed: %r" % exc)
            continue
        for ans in got:
            t = truth[ans["name"]]
            n += 1
            same_act = ans["action"] == t["action"]
            ok_action += same_act
            want_cls = t.get("class") or ""
            same_cls = (ans["class"] or "") == want_cls
            ok_class += same_cls
            if not (same_act and same_cls):
                wrong.append((ans["name"], ans["action"], ans["class"],
                              t["action"], want_cls))
    if not n:
        print("no answers parsed; check the endpoint and the key")
        return 1
    print("\nscored %d names" % n)
    print("  action agreed : %d/%d (%.1f%%)" % (ok_action, n,
                                                100.0 * ok_action / n))
    print("  class agreed  : %d/%d (%.1f%%)" % (ok_class, n,
                                                100.0 * ok_class / n))
    for name, act, cls, tact, tcls in wrong[:15]:
        print("  \\%-14s said %s/%s, TeX says %s/%s"
              % (name, act, cls, tact, tcls))
    return 0

CORPUS = None
STEP = 1
#: collect names as if no name table existed.  A name the table already settles
#: (``\cr`` is ignored, ``\hbox`` unwrapped) no longer appears in the output, so
#: collecting from the pipeline as it stands would drop it from the list and the
#: next rebuild would forget the decision.
EMPTY_TABLE = False


def _normaliser(paper: str = ""):
    if EMPTY_TABLE:
        return N.Normalizer(N.NameTable(tex_path="", answers_path=""),
                            paper=paper)
    return N.Normalizer(paper=paper)


def _rows(corpus: str, step: int):
    man = os.path.join(corpus, "manifest.tsv")
    with open(man, encoding="utf-8") as fh:
        next(fh)
        return [(p[0], p[5]) for p in (l.rstrip("\n").split("\t") for l in fh)
                if len(p) >= 6][::step]


def _in_math(text: str, pos: int) -> bool:
    r"""Cheap: is ``pos`` inside maths on its line?

    ``$`` is the common case, but ``$$`` and ``\[`` open display maths and
    ``\(`` opens inline maths; counting only ``$`` labelled every name in those
    spans "in text".
    """
    line_start = text.rfind("\n", 0, pos) + 1
    window = text[line_start:pos]
    if window.count(r"\[") > window.count(r"\]"):
        return True
    if window.count(r"\(") > window.count(r"\)"):
        return True
    if len(re.findall(r"(?<!\\)\$\$", window)) % 2 == 1:
        return True
    dollars = re.sub(r"\\.", "", window).count("$")
    return dollars % 2 == 1


def _shape(text: str, pos: int, end: int) -> str:
    """How the name is called: how many groups follow it, and where it sits."""
    i = end
    groups = 0
    while groups < 3:
        j = i
        while j < len(text) and text[j] in " \t\n":
            j += 1
        if j < len(text) and text[j] in "{[":
            k = L.find_group_end(text, j)
            if k is None:
                break
            groups += 1
            i = k
            continue
        break
    where = "math" if _in_math(text, pos) else "text"
    return "%d group(s) follow, in %s" % (groups, where)


def _excerpt(text: str, pos: int, width: int = 90) -> str:
    lo = max(0, pos - width)
    hi = min(len(text), pos + width)
    return " ".join(text[lo:hi].split())


def _definition_snippet(full: str, name: str) -> str:
    m = re.search(r"\\(?:[gex]?def|let|(?:re)?newcommand|providecommand|"
                  r"Declare\w+)\*?\s*\{?\s*\\" + re.escape(name)
                  + r"(?![A-Za-z@])", full)
    if not m:
        return ""
    return full[max(0, m.start() - 40):m.end() + 80].replace("\n", " ")[:240]


def one(job):
    pid, root = job
    corpus = CORPUS
    tex = os.path.join(corpus, pid, root)
    try:
        src = N.read_source(tex)
        if not src.strip():
            return pid, {"__error__": {"count": 0, "papers": [pid],
                                       "examples": ["empty or missing source"]}}
        styles = N.local_styles(tex, src)
        n = _normaliser(pid)
        out = n.normalize(src, styles)
        outside = n.vocabulary(out)
        kept = set(getattr(n, "kept_macros", {}) or {})
        raw = n.vocabulary(out, count_allowed_names=False)
        if not outside:
            return pid, {}
        full = L.strip_comments(src)
        for st in styles:
            full += "\n" + open(st, encoding="utf-8", errors="replace").read()
        m = L.MacroTable()
        m.parse(full)
        N.augment_macros(m, full)
        m.parse(full)
        rec = {}
        allowed_hits = {k: raw.get(k, 0) for k in raw if k not in outside}
        for name, cnt in outside.items():
            if "@" in name:
                continue
            rec[name] = {
                "count": cnt,
                "papers": [pid],
                "settled": bool(n.names.known(name)),
                "shape": "",
                "examples": [],
            }
            if rec[name]["settled"]:
                continue
            if name in m.macros or name in m._cyclic_names():
                # never silent: the question layer has to know the name exists
                # and why it is still open (OPEN.md 1.1, step 1)
                rec[name]["reason"] = ("kept_macro" if name in kept
                                       else "defined_but_unexpanded")
                rec[name]["definition"] = (
                    kept.get(name) or (m.macros.get(name) or (0, "", None))[1])[:300]
                if name in m._cyclic_names():
                    rec[name]["reason"] = "cyclic_definition"
                hits = [(mm.start(), mm.end())
                        for mm in re.finditer(r"\\" + re.escape(name)
                                              + r"(?![A-Za-z@])", out)][:2]
                rec[name]["examples"] = [_excerpt(out, h[0]) for h in hits]
                continue
            if re.search(r"\\(?:[gex]?def|let|(?:re)?newcommand|"
                         r"providecommand|Declare\w+)\*?\s*\{?\s*\\"
                         + re.escape(name) + r"(?![A-Za-z@])", full):
                rec[name]["reason"] = "definition_not_parsed"
                rec[name]["definition"] = _definition_snippet(full, name)
                continue
            hits = [(mm.start(), mm.end())
                    for mm in re.finditer(r"\\" + re.escape(name)
                                          + r"(?![A-Za-z@])", out)][:3]
            rec[name]["shape"] = _shape(out, *hits[0]) if hits else ""
            rec[name]["examples"] = [_excerpt(out, h[0]) for h in hits]
            if name in kept:
                # the definition body is the evidence for a macro that was kept
                # instead of expanded (OPEN-PROBLEMS.md 7.1 item 3)
                rec[name]["shape"] = rec[name]["shape"] or "kept macro"
                rec[name]["examples"] = [kept[name]] + rec[name]["examples"]
        for name, cnt in allowed_hits.items():
            rec[name] = {"count": cnt, "papers": [pid], "settled": True,
                         "shape": "", "examples": []}
        return pid, rec
    except Exception as exc:                       # noqa: BLE001
        return pid, {"__error__": {"count": 1, "examples": [repr(exc)],
                                   "shape": ""}}


def collect(step: int, corpus: str) -> dict:
    global CORPUS, STEP
    CORPUS, STEP = corpus, step
    rows = _rows(corpus, step)
    with mp.Pool(min(16, os.cpu_count() or 4), maxtasksperchild=40) as pool:
        recs = pool.map(one, rows, chunksize=4)
    agg: dict = {}
    failed = []
    for pid, rec in recs:
        if "__error__" in rec:
            # A paper that raised is not a command: it used to become the name
            # "__error__", which --export asked about and --merge could persist.
            failed.append(pid)
            continue
        for name, info in rec.items():
            slot = agg.setdefault(name, {"count": 0, "papers": [] ,
                                         "settled": False,
                                         "shapes": collections.Counter(),
                                         "reasons": collections.Counter(),
                                         "definition": "",
                                         "examples": []})
            slot["count"] += info["count"]
            slot["papers"].extend(info.get("papers") or [pid])
            slot["settled"] = slot["settled"] or info.get("settled", False)
            if info.get("reason"):
                slot["reasons"][info["reason"]] += 1
            if info.get("definition") and not slot.get("definition"):
                slot["definition"] = info["definition"]
            if info.get("shape"):
                slot["shapes"][info["shape"]] += 1
            for ex in info["examples"]:
                if len(slot["examples"]) < 3 and ex not in slot["examples"]:
                    slot["examples"].append(ex)
    for name, slot in agg.items():
        slot["shapes"] = dict(slot["shapes"].most_common(3))
        slot["reasons"] = dict(slot["reasons"].most_common(3))
    if failed:
        print("  %d papers failed and were not counted: %s"
              % (len(failed), ", ".join(failed[:5])))
    return agg


def do_export(a) -> int:
    agg = collect(a.step, a.corpus)
    agg = {n: v for n, v in agg.items() if not v["settled"]}
    order = sorted(agg.items(), key=lambda kv: -kv[1]["count"])
    if a.limit:
        order = order[:a.limit]
    items = []
    for name, slot in order:
        slot["papers"] = sorted(set(slot["papers"]))
        item = {
            "name": name,
            "count": slot["count"],
            "papers": len(slot["papers"]),
            "shape": (list(slot["shapes"]) or [""])[0],
            "examples": slot["examples"],
            "action": None, "class": None, "body": None, "note": None,
            "scope": "text",          # vocabulary() reads the prose, not formulas
            "reason": (list(slot.get("reasons") or {}) or [None])[0],
            "definition": slot.get("definition") or "",
        }
        if len(slot["papers"]) == 1:
            # the name appears in one paper only: answer it for that paper, so
            # another paper using the same name differently is not overridden
            item["paper"] = slot["papers"][0]
        items.append(item)
    doc = {
        "_about": "Command names that TeX does not define and the paper does "
                  "not define, so only a reader can say what they are.  Fill "
                  "in `action` (and `class` for allow/font, `body` for "
                  "expand), then merge with `pending_names.py --merge`.",
        "how_to_answer": {
            "allow": "it is a math symbol or an operator: keep it; give "
                     "`class` one of the allowed classes",
            "font": "it only styles its argument (a font switch like "
                    "\\mathbb): keep it; class `font`",
            "unwrap": "it takes one group holding the content: keep the "
                      "group, drop the name",
            "ignore": "it carries nothing of its own: drop the name",
            "expand": "it stands for something expressible in the vocabulary: "
                      "give `body` (no `@`, must not mention itself)",
            "unknown": "cannot be decided without the paper's own convention; "
                       "leave `action` null and say why in `note`",
        },
        "allowed_actions": list(N.NAME_ACTIONS) + ["unknown"],
        "allowed_classes": list(N.NAME_CLASSES),
        "items": items,
    }
    with open(a.export, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    print("open names=%d written to %s (showing %d)"
          % (len(agg), a.export, len(items)))
    by_reason: collections.Counter = collections.Counter()
    for _n, slot in agg.items():
        for reason, n in (slot.get("reasons") or {}).items():
            by_reason[reason] += n
    if by_reason:
        print("  by reason: %s" % ", ".join("%s=%d" % kv
                                            for kv in by_reason.most_common()))
    for name, slot in order:
        print("  %-16s %5d uses in %3d papers  %s"
              % ("\\" + name, slot["count"], len(set(slot["papers"])),
                 (list(slot["shapes"]) or [""])[0]))
    return 0


def do_merge(a) -> int:
    with open(a.merge, encoding="utf-8") as fh:
        doc = json.load(fh)
    tex = N.NameTable.__new__(N.NameTable)
    N.NameTable.__init__(tex, answers_path="")     # only TeX decisions
    current = {}
    papers: dict = {}
    # A missing file is a first run; a file that does not parse is a broken
    # answer library.  Treating the two alike meant one truncated write became
    # "no answers yet" and the next merge overwrote everything with silence.
    try:
        with open(ANSWERS_PATH, encoding="utf-8") as fh:
            doc_old = json.load(fh)
    except FileNotFoundError:
        doc_old = {}
    except ValueError as exc:
        print("refusing: %s is not valid JSON (%s)" % (ANSWERS_PATH, exc))
        print("  repair it, or move it aside, before merging")
        return 2
    except OSError as exc:
        print("refusing: cannot read %s (%s)" % (ANSWERS_PATH, exc))
        return 2
    current = doc_old.get("names") or {}
    papers = doc_old.get("papers") or {}
    added, changed, rejected = 0, 0, []
    refused: list = []
    for item in doc.get("items") or []:
        name = (item.get("name") or "").strip().lstrip("\\")
        action = item.get("action")
        if not name or action in (None, "unknown"):
            if name:
                refused.append(name)
            continue
        if name in tex.origin:
            rejected.append((name, "TeX already decides it (%s)"
                             % tex.origin[name]))
            continue
        if action not in N.NAME_ACTIONS:
            rejected.append((name, "action %r not allowed" % action))
            continue
        rec = {"action": action, "source": "answer"}
        if action in ("allow", "font"):
            cls = item.get("class") or ("font" if action == "font" else "")
            if cls not in N.NAME_CLASSES:
                rejected.append((name, "class %r not allowed" % cls))
                continue
            if (action == "font") != (cls == "font"):
                rejected.append((name, "action %r with class %r" % (action, cls)))
                continue
            rec["class"] = cls
        if action == "expand":
            body = (item.get("body") or "").strip()
            ok, why = _plausible_body(body)
            if not ok:
                rejected.append((name, "expansion " + why))
                continue
            probe = N.NameTable(tex_path="", answers_path="")
            probe._add(name, {"action": "expand", "body": body}, "answer")
            if name not in probe.expand:
                rejected.append((name, "body missing, empty, or self-referential"))
                continue
            rec["body"] = body
        if item.get("note"):
            rec["note"] = item["note"]
        paper = (item.get("paper") or "").strip()
        if paper:
            bucket = papers.setdefault(paper, {})
            if bucket.get(name, {}).get("action") != action or \
                    bucket.get(name, {}).get("body") != rec.get("body") or \
                    bucket.get(name, {}).get("class") != rec.get("class"):
                changed += 1
            bucket[name] = rec
            continue
        if name in current:
            if current[name].get("action") != action or \
                    current[name].get("body") != rec.get("body") or \
                    current[name].get("class") != rec.get("class"):
                changed += 1
        else:
            added += 1
        current[name] = rec
    out = {
        "_about": "Answers for command names TeX does not define and the paper "
                  "does not define.  `names` applies to every paper; `papers` "
                  "holds what one paper says about its own notation, which "
                  "overrides a general answer but never a TeX decision.  "
                  "Written by `pending_names.py --merge`, read by "
                  "`normalize.py` through NameTable.",
        "names": {k: current[k] for k in sorted(current)},
        "papers": {p: {n: papers[p][n] for n in sorted(papers[p])}
                   for p in sorted(papers)},
    }
    tmp = ANSWERS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, ANSWERS_PATH)
    print("merged: added=%d changed=%d global=%d papers=%d"
          % (added, changed, len(current),
             sum(len(v) for v in papers.values())))
    for name, why in rejected[:20]:
        print("  rejected \\%s: %s" % (name, why))
    if refused:
        print("  %d answers left undecided (action null/unknown): %s"
              % (len(refused), ", ".join(refused[:10])))
    # a refused or undecided answer must not look like a successful merge
    return 1 if (rejected or refused) else 0


def do_report(a) -> int:
    agg = collect(a.step, a.corpus)
    names = N.NameTable()
    settled = {n: v for n, v in agg.items() if v["settled"]}
    by_origin = collections.Counter()
    for n in settled:
        by_origin[names.origin.get(n, "paper")] += 1
    print("commands outside the vocabulary: %d distinct, %d uses"
          % (len(agg), sum(v["count"] for v in agg.values())))
    print("commands outside the vocabulary, counting only what TeX or an "
          "answer did not settle:")
    print("  accounted for: %d names %s" % (len(settled), dict(by_origin)))
    print("     (%d of them by an allow/font entry, so they no longer appear "
          "in the count at all)" % sum(
              1 for n in settled if n in names.allowed))
    rest = sorted((v["count"], n) for n, v in agg.items() if not v["settled"])
    print("  still open:    %d names, %d uses" % (len(rest),
                                                  sum(c for c, _ in rest)))
    per_paper = sum(len(v) for v in (names.paper_names or {}).values())
    if per_paper:
        print("  of which answered for one paper: %d (in %d papers)"
              % (per_paper, len(names.paper_names or {})))
    for count, name in list(reversed(rest))[:12]:
        print("     %-16s %5d" % ("\\" + name, count))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=os.path.join(GTED, "newarticle"))
    ap.add_argument("--step", type=int, default=14)
    ap.add_argument("--export", metavar="FILE")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--merge", metavar="FILE")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--answer-with-model", metavar="FILE",
                    help="fill in the answers of an exported file (see "
                         "NL_PREP_* variables below)")
    ap.add_argument("--calibrate", action="store_true",
                    help="score a backend against the names TeX decided")
    ap.add_argument("--backend", default="",
                    help="api-openai | api-anthropic | local-openai "
                         "(default: $NL_PREP_BACKEND or api-openai)")
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the prompt instead of calling anything")
    ap.add_argument("--check-config", action="store_true",
                    help="show which endpoint and key a run would use")
    a = ap.parse_args(argv)
    if a.step < 1:
        ap.error("--step must be 1 or more (got %d)" % a.step)
    if a.check_config:
        return do_check_config(a)
    chosen = [n for n in ("export", "report", "merge", "calibrate",
                          "answer_with_model")
              if getattr(a, n.replace("-", "_"), None)]
    if len(chosen) > 1:
        ap.error("pick one of --%s" % ", --".join(c.replace("_", "-")
                                                  for c in chosen))
    if a.export:
        return do_export(a)
    if a.merge:
        return do_merge(a)
    if a.answer_with_model:
        return do_answer(a)
    if a.calibrate:
        return do_calibrate(a)
    if a.report:
        return do_report(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
