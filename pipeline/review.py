"""Serve the local review page for a pipeline output directory."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "scripts"))
from review_catalog import catalog, change, read
from manual_review import Changed
import pending_names
import review_model


class Reviewer(ThreadingHTTPServer):
    def __init__(self, out, port):
        super().__init__(("127.0.0.1", port), Handler)
        self.out = out
        self.url = "http://127.0.0.1:%d" % self.server_port
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.job = {"running": False, "result": None}
        self.generation = 0
        self.model_job = {"running": False}
        self.model_stop = threading.Event()

    def start_model(self, request):
        if self.model_job["running"]:
            raise Changed("已有模型审核正在运行。")
        state = catalog(self.out)
        if request.get("paper") != state["paper"]:
            raise Changed("稿件版本已变更，请刷新。")
        scope = request.get("scope")
        items = [i for i in state["items"] if i["model_eligible"]]
        if scope == "item":
            items = [i for i in items if (i["kind"], i["target"], i["version"]) ==
                     (request.get("kind"), request.get("target"), request.get("version"))]
        elif scope == "required":
            items = [i for i in items if i["required"]]
        elif scope != "all":
            raise ValueError("请选择本条、必办或全部可判断条目。")
        if not items:
            raise Changed("选定范围内没有可由模型判断的未确认条目，请刷新。")
        cfg = pending_names.backend_config()
        self.model_stop.clear()
        self.model_job = dict(running=True, model=cfg["model"], total=len(items), processed=0,
                              saved=0, skipped=0, abstained=0, failed=0, disagreements=0, details=[])
        threading.Thread(target=self.run_model, args=(cfg, state["paper"], state["classes"], items),
                         daemon=True).start()

    def run_model(self, cfg, paper, classes, items):
        job = self.model_job
        try:
            for item in items:
                if self.model_stop.is_set():
                    break
                outcome, message, fatal = "abstained", "", False
                try:
                    answer = review_model.judge(cfg, item, classes)
                    with self.lock:
                        if self.model_stop.is_set():
                            break
                        if answer is not None:
                            request = dict(paper=paper, kind=item["kind"], target=item["target"],
                                           version=item["version"], op="save", **answer)
                            decision = change(self.out, request, model=cfg["model"])
                            outcome = "saved"
                            if decision.get("comparison", {}).get("status") == "disagreement":
                                job["disagreements"] += 1
                            self.generation += 1
                        else:
                            message = "模型弃权"
                except Changed as exc:
                    outcome, message = "skipped", str(exc)
                except ConnectionError as exc:
                    outcome, message, fatal = "failed", str(exc), True
                except (ValueError, TypeError, KeyError, OSError) as exc:
                    outcome, message = "failed", str(exc)
                with self.lock:
                    job["processed"] += 1
                    job[outcome] += 1
                    if message:
                        job["details"].append(dict(kind=item["kind"], location=item["location"], message=message))
                if fatal:
                    break
        finally:
            with self.lock:
                job.update(running=False, stopped=self.model_stop.is_set())
                self.generation += 1

    def launch(self, paper):
        stats = read(self.out, "normalize-stats.json")
        source = Path(stats.get("input") or "")
        if stats.get("paper_id") != paper or not source.is_file():
            raise ValueError("本机找不到这份原稿。请在原运行环境重跑切分。")
        git_bash = Path(os.environ.get("ProgramFiles", "")) / "Git/bin/bash.exe"
        shell = str(git_bash) if os.name == "nt" and git_bash.is_file() else shutil.which("bash")
        if not shell:
            raise ValueError("找不到 Bash。可从原入口重跑。")
        cmd = [shell, "pipeline/scripts/run_pipeline.sh", source.resolve().as_posix(),
               self.out.as_posix(), "--no-ask"]
        engine = read(self.out, "execution.json").get("engine")
        if engine in ("pdflatex","xelatex","lualatex"):
            cmd += ["--tex-engine", engine]
        process = subprocess.Popen(cmd, cwd=HERE.parent, env=dict(os.environ, PYTHON=sys.executable),
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   encoding="utf-8", errors="replace")
        self.job = {"running": True, "result": None}
        def finish():
            log, _ = process.communicate()
            with self.lock:
                self.job = {"running": False, "result":process.returncode,
                            "message":log[-1200:] if process.returncode not in (0,3) else ""}
                self.generation += 1
        threading.Thread(target=finish, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, body, mime="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def local(self):
        return self.headers.get("Host", "") == self.server.url.split("//")[1]

    def do_GET(self):
        if not self.local():
            return self.reply(403, {})
        paths = {"/":("index.html","text/html"), "/review.css":("review.css","text/css"),
                 "/review.js":("review.js","text/javascript")}
        if self.path in paths:
            file, mime = paths[self.path]
            body = (HERE / "review_ui" / file).read_bytes()
            if file == "index.html":
                body = body.replace(b"__REVIEW_TOKEN__", self.server.token.encode())
            return self.reply(200, body, mime + "; charset=utf-8")
        try:
            with self.server.lock:
                if self.path == "/api/status":
                    return self.reply(200, {**self.server.job, "generation":self.server.generation, "model_job": self.server.model_job})
                if self.path == "/api/items" and not self.server.job["running"]:
                    state = catalog(self.server.out)
                    try:
                        cfg = pending_names.backend_config()
                        state["model"] = {"name": cfg["model"], "backend": cfg["backend"]}
                    except ValueError as exc:
                        state["model"] = {"error": str(exc)}
                    return self.reply(200, state)
                if self.path == "/api/items":
                    return self.reply(409, {"error":"复验正在运行，请稍候。"})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return self.reply(422, {"error":str(exc)})
        self.reply(404, {})

    def do_POST(self):
        if (not self.local() or self.headers.get("Origin",self.server.url) != self.server.url
                or self.headers.get("X-Review-Token") != self.server.token):
            return self.reply(403, {})
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 131072:
                raise ValueError("请求长度无效。")
            obj = json.loads(self.rfile.read(size))
            if not isinstance(obj, dict):
                raise ValueError("请求格式无效。")
            with self.server.lock:
                if self.server.job["running"]:
                    raise Changed("复验中，请稍候。")
                if self.path == "/api/decision":
                    change(self.server.out, obj)
                elif self.path == "/api/recheck":
                    if self.server.model_job["running"]:
                        raise Changed("请等待模型审核完成或停止审核，再应用并复验。")
                    self.server.launch(obj.get("paper"))
                elif self.path == "/api/model-review":
                    self.server.start_model(obj)
                elif self.path == "/api/model-stop":
                    self.server.model_stop.set()
                else:
                    return self.reply(404, {})
                return self.reply(200, {"ok":True})
        except Changed as exc:
            self.reply(409, {"error":str(exc)})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.reply(400, {"error":str(exc)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, help="existing pipeline output directory")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--open", action="store_true", help="open the review page in a browser")
    args = parser.parse_args()
    out = args.out.resolve()
    if not (out / "formulas.json").is_file():
        parser.error("请选择包含 formulas.json 的切分输出目录")
    catalog(out)
    with Reviewer(out, args.port) as server:
        print(server.url + "/", flush=True)
        if args.open:
            webbrowser.open(server.url + "/")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
