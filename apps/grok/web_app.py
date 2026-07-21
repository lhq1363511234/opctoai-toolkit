"""Web UI for grokRegister-cpa - Flask backend with SSE log streaming.

Adapted to Git-creat7/grokRegister-cpa:
  SSO -> pure HTTP OIDC -> local CPA auth dir + remote Management API upload.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent
# Prefer container-friendly writable locations. All workflow deps should be form-configurable.
DATA_DIR = Path(os.environ.get("GROK_DATA_DIR") or (BASE_DIR / "data" if (BASE_DIR / "data").exists() else BASE_DIR))
if str(os.environ.get("GROK_DATA_DIR") or "").strip():
    DATA_DIR = Path(os.environ["GROK_DATA_DIR"]).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)

_cfg_env = (os.environ.get("GROK_CONFIG_FILE") or "").strip()
if _cfg_env:
    CONFIG_FILE = Path(_cfg_env).expanduser()
elif (Path("/app/config.json")).exists() or os.environ.get("PORT"):
    # container mode: keep config on /app (mounted volume) when present, else data dir
    CONFIG_FILE = Path("/app/config.json") if Path("/app").exists() else (DATA_DIR / "config.json")
else:
    CONFIG_FILE = BASE_DIR / "config.json"
CONFIG_EXAMPLE = BASE_DIR / "config.example.upstream.json"
ACCOUNTS_FILE = DATA_DIR / "accounts_cli.txt"
CPA_DIR = Path(os.environ.get("GROK_CPA_DIR") or (DATA_DIR / "cpa_auths"))
CPA_DIR.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(BASE_DIR))

app = Flask(__name__, template_folder="templates", static_folder="static")

# --------------- SSE log bus ---------------
_log_listeners: list[queue.Queue] = []
_log_lock = threading.Lock()
_log_history: list[dict] = []
MAX_HISTORY = 500

_run_lock = threading.Lock()
_running = False
_stats = dict(reg_success=0, reg_fail=0, mint_success=0, mint_fail=0, mint_skip=0)
_cancel_event = threading.Event()

# Container/host web process may be PID1 for chrome children; reap zombies + stale tmp.
try:
    from browser_session import start_background_reaper
    start_background_reaper(interval_sec=45.0)
except Exception:
    pass
_runner_controller = None


def _broadcast(entry: dict):
    with _log_lock:
        _log_history.append(entry)
        if len(_log_history) > MAX_HISTORY:
            _log_history.pop(0)
        dead = []
        for q in _log_listeners:
            try:
                q.put_nowait(entry)
            except Exception:
                dead.append(q)
        for q in dead:
            _log_listeners.remove(q)


def _subscribe():
    q = queue.Queue(maxsize=200)
    with _log_lock:
        _log_listeners.append(q)
    return q


def _unsubscribe(q):
    with _log_lock:
        try:
            _log_listeners.remove(q)
        except ValueError:
            pass


def _is_running():
    with _run_lock:
        return _running


def _strip_comments(obj):
    if not isinstance(obj, dict):
        return {}
    return {k: v for k, v in obj.items() if not str(k).startswith("//") and not str(k).startswith("#")}


def load_config():
    path = CONFIG_FILE if CONFIG_FILE.exists() else CONFIG_EXAMPLE
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="gb18030")
    return _strip_comments(json.loads(content))


def save_config(data):
    existing = {}
    if CONFIG_FILE.exists():
        try:
            content = CONFIG_FILE.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = CONFIG_FILE.read_text(encoding="gb18030")
        existing = json.loads(content)

    for k, v in (data or {}).items():
        existing[k] = v

    # keep register_threads mirrored for older UI fields
    if "register_workers" in existing and "register_threads" not in data:
        existing["register_threads"] = existing.get("register_workers")
    if "register_threads" in existing and "register_workers" not in existing:
        existing["register_workers"] = existing.get("register_threads")

    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return existing


def cpa_file_count():
    if not CPA_DIR.exists():
        return 0
    return len(list(CPA_DIR.glob("xai-*.json")))


def read_accounts():
    results = []
    # collect newest accounts_*.txt + accounts_cli.txt
    account_files = sorted(BASE_DIR.glob("accounts_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if ACCOUNTS_FILE.exists():
        account_files = [ACCOUNTS_FILE] + [p for p in account_files if p.resolve() != ACCOUNTS_FILE.resolve()]

    seen = set()
    for af in account_files[:5]:
        try:
            lines = af.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("----")
            email = parts[0] if len(parts) > 0 else ""
            password = parts[1] if len(parts) > 1 else ""
            sso = parts[2] if len(parts) > 2 else ""
            if not email or email in seen:
                continue
            seen.add(email)

            cpa_path = CPA_DIR / f"xai-{email}.json"
            # also accept hashed filenames written by upstream cpa_auth_filename
            has_cpa = cpa_path.exists()
            cpa_expiry = ""
            cpa_raw = ""
            matched = None
            if has_cpa:
                matched = cpa_path
            else:
                for cand in CPA_DIR.glob("xai-*.json"):
                    try:
                        data = json.loads(cand.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if str(data.get("email") or "").lower() == email.lower():
                        matched = cand
                        has_cpa = True
                        break
            if matched and matched.exists():
                try:
                    cpa_data = json.loads(matched.read_text(encoding="utf-8"))
                    cpa_raw = json.dumps(cpa_data, ensure_ascii=False)
                    exp = cpa_data.get("expired") or cpa_data.get("expires_at") or cpa_data.get("expires")
                    if exp:
                        cpa_expiry = str(exp)
                except Exception:
                    pass

            results.append(
                dict(
                    email=email,
                    password=password,
                    sso=sso,
                    has_cpa=has_cpa,
                    cpa_expiry=cpa_expiry,
                    cpa_raw=cpa_raw,
                    source=af.name,
                )
            )
    return results


def _hotload_copy_if_enabled(cfg: dict, log_cb):
    """Optional: copy local CPA files into an explicitly configured hotload dir.

    External mode default is OFF. Never fall back to host cliproxy paths.
    """
    if not cfg.get("cpa_copy_to_hotload"):
        return
    hot_raw = str(cfg.get("cpa_hotload_dir") or "").strip()
    if not hot_raw:
        log_cb("[CPA] 已开启热加载复制，但未配置 cpa_hotload_dir，已跳过（外接模式不写本机 cliproxy）")
        return
    hot = Path(hot_raw)
    src_dir = Path(str(cfg.get("cpa_auth_dir") or "").strip() or str(CPA_DIR))
    if not src_dir.exists():
        return
    try:
        hot.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        log_cb(f"[CPA] 热加载目录不可用: {hot} ({exc})")
        return
    n = 0
    for src in src_dir.glob("xai-*.json"):
        try:
            dst = hot / src.name
            if not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime:
                shutil.copy2(src, dst)
                n += 1
        except Exception as exc:
            log_cb(f"[CPA] 复制失败 {src.name}: {exc}")
    if n:
        log_cb(f"[CPA] 已同步 {n} 个凭证到热加载目录 {hot}")


def _run_registration(extra: int, workers: int):
    global _running, _stats, _runner_controller
    with _run_lock:
        if _running:
            return
        _running = True
        _stats = dict(reg_success=0, reg_fail=0, mint_success=0, mint_fail=0, mint_skip=0)
    _cancel_event.clear()

    def log_cb(msg: str):
        _broadcast(dict(ts=time.strftime("%H:%M:%S"), msg=str(msg)))

    try:
        import grok_register_ttk as reg

        # reload config into module globals
        reg.load_config()
        if hasattr(reg, '_wire_runtime_modules'):
            reg._wire_runtime_modules()
        cfg = load_config()
        # push latest web config into module config
        for k, v in cfg.items():
            reg.config[k] = v

        workers = max(1, min(int(workers or 1), 8, int(extra or 1)))
        reg.config["register_workers"] = workers
        reg.config["register_count"] = int(extra or 1)

        # ensure local CPA dir exists (container-local /data by default)
        auth_dir = str(reg.config.get("cpa_auth_dir") or str(CPA_DIR)).strip() or str(CPA_DIR)
        reg.config["cpa_auth_dir"] = auth_dir
        Path(auth_dir).mkdir(parents=True, exist_ok=True)

        report = _external_dependency_report(reg.config)
        log_cb(f"[Web] 开始注册: 数量={extra}, 并发={workers}")
        log_cb(f"[Web] 外接模式: {'就绪' if report.get('external_ok') else '待完善'}")
        if report.get("local_dependencies"):
            log_cb(f"[Web] 仍依赖本机项: {', '.join(report['local_dependencies'])}")
        if report.get("missing_fields"):
            log_cb(f"[Web] 缺少外接配置: {', '.join(report['missing_fields'])}")
        log_cb(f"[Web] 邮箱: provider={reg.config.get('email_provider')} base={reg.config.get('cloudflare_api_base') or reg.config.get('cloudmail_url') or ''}")
        log_cb(f"[Web] 代理: {reg.config.get('proxy') or '(直连/外接未填)'}")
        log_cb(f"[Web] CPA 直出: {'开' if reg.config.get('cpa_auto_add') else '关'}")
        log_cb(f"[Web] CPA 本地: {reg.config.get('cpa_auth_dir') or '(空)'}")
        log_cb(f"[Web] CPA 远程: {reg.config.get('cpa_remote_url') or '(空)'}")
        log_cb(f"[Web] 热加载复制: {'开' if reg.config.get('cpa_copy_to_hotload') else '关'} -> {reg.config.get('cpa_hotload_dir') or '(未配置)'}")

        # patch cli_log + capture stop controller
        original_cli_log = reg.cli_log

        def patched_cli_log(message):
            original_cli_log(message)
            # original already filters debug; still broadcast raw for web
            if message and not str(message).startswith("__"):
                # avoid double timestamps if already present
                _broadcast(dict(ts=time.strftime("%H:%M:%S"), msg=str(message)))

        reg.cli_log = patched_cli_log

        # wrap CliStopController creation by patching class used inside run_registration_cli
        OriginalController = reg.CliStopController

        class TrackingController(OriginalController):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                global _runner_controller
                _runner_controller = self

            def stop(self):
                super().stop()
                _cancel_event.set()

        reg.CliStopController = TrackingController

        # run
        # run_registration_cli() installs SIGINT handlers; that only works in the
        # main thread. Web UI runs registration in a worker thread, so patch
        # signal.signal to no-op when not on the main interpreter thread.
        import signal as _signal
        _real_signal = _signal.signal
        def _thread_safe_signal(sig, handler):
            if threading.current_thread() is not threading.main_thread():
                return _signal.SIG_DFL
            return _real_signal(sig, handler)
        _signal.signal = _thread_safe_signal

        before_files = {p.name for p in Path(auth_dir).glob("xai-*.json")} if Path(auth_dir).exists() else set()
        try:
            reg.run_registration_cli(int(extra or 1))
        finally:
            try:
                _signal.signal = _real_signal
            except Exception:
                pass
            reg.cli_log = original_cli_log
            reg.CliStopController = OriginalController
            _runner_controller = None

        after_files = {p.name for p in Path(auth_dir).glob("xai-*.json")} if Path(auth_dir).exists() else set()
        new_files = sorted(after_files - before_files)
        _stats["reg_success"] = len(new_files) if new_files else _stats.get("reg_success", 0)
        _stats["mint_success"] = len(new_files)
        if new_files:
            log_cb(f"[Web] 新增 CPA 凭证: {len(new_files)}")
            for name in new_files[:20]:
                log_cb(f"[CPA] + {name}")

        # also mirror into accounts_cli.txt for UI list convenience
        try:
            newest = sorted(BASE_DIR.glob("accounts_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
            if newest:
                # append unique lines into accounts_cli.txt
                existing = set()
                if ACCOUNTS_FILE.exists():
                    existing = {ln.strip() for ln in ACCOUNTS_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()}
                added = 0
                with ACCOUNTS_FILE.open("a", encoding="utf-8") as out:
                    for ln in newest[0].read_text(encoding="utf-8").splitlines():
                        s = ln.strip()
                        if s and s not in existing:
                            out.write(s + "\n")
                            existing.add(s)
                            added += 1
                if added:
                    log_cb(f"[Web] 已汇总 {added} 条账号到 accounts_cli.txt")
        except Exception as exc:
            log_cb(f"[Web] 汇总账号文件失败: {exc}")

        _hotload_copy_if_enabled(reg.config, log_cb)
        log_cb(
            f"[Web] 注册结束: CPA新增={_stats['mint_success']} running_cancel={_cancel_event.is_set()}"
        )
    except Exception as exc:
        log_cb(f"[!] 任务异常: {exc}")
        log_cb(traceback.format_exc())
    finally:
        # 任务结束/停止后兜底清理残留浏览器，避免多 worker 遗留 chromium
        try:
            from browser_session import (
                cleanup_orphan_register_browsers,
                cleanup_browser_temp_dirs,
                reap_zombie_children,
            )
            n = cleanup_orphan_register_browsers(force=True) or 0
            tmp = cleanup_browser_temp_dirs(force=True, max_age_sec=60)
            z = reap_zombie_children()
            _broadcast(dict(
                ts=time.strftime("%H:%M:%S"),
                msg=(
                    f"[Web] 结束兜底清理: browser_dirs={n} "
                    f"tmp_dirs={tmp.get('removed_dirs', 0)} zombies={z}"
                ),
            ))
        except Exception as exc:
            _broadcast(dict(ts=time.strftime("%H:%M:%S"), msg=f"[Web] 结束清理浏览器失败: {exc}"))
        with _run_lock:
            _running = False
        _broadcast(dict(ts=time.strftime("%H:%M:%S"), msg="__DONE__"))


@app.route("/")
def index():
    return render_template("index.html")


def _external_dependency_report(cfg: dict | None = None) -> dict:
    """Report whether workflow still points at host-local services."""
    cfg = cfg or load_config()
    local_markers = []
    proxy = str(cfg.get("proxy") or "").strip()
    cpa_proxy = str(cfg.get("cpa_proxy") or "").strip()
    hotload = str(cfg.get("cpa_hotload_dir") or "").strip()
    remote = str(cfg.get("cpa_remote_url") or "").strip()
    mail_base = str(cfg.get("cloudflare_api_base") or cfg.get("cloudmail_url") or "").strip()
    auth_dir = str(cfg.get("cpa_auth_dir") or "").strip() or str(CPA_DIR)

    def _is_loopback(url: str) -> bool:
        u = (url or "").lower()
        return any(x in u for x in ("127.0.0.1", "localhost", "0.0.0.0", "::1"))

    if proxy and _is_loopback(proxy):
        local_markers.append(f"proxy={proxy}")
    if cpa_proxy and _is_loopback(cpa_proxy):
        local_markers.append(f"cpa_proxy={cpa_proxy}")
    if cfg.get("cpa_copy_to_hotload"):
        if not hotload:
            local_markers.append("cpa_copy_to_hotload=true 但未配置 cpa_hotload_dir")
        elif "/var/lib/cliproxyapi" in hotload or _is_loopback(hotload):
            local_markers.append(f"cpa_hotload_dir={hotload}")
    if remote and _is_loopback(remote):
        local_markers.append(f"cpa_remote_url={remote}")
    if mail_base and _is_loopback(mail_base):
        local_markers.append(f"mail={mail_base}")
    if auth_dir.startswith("/root/") or auth_dir.startswith("/var/lib/cliproxyapi"):
        local_markers.append(f"cpa_auth_dir={auth_dir}")

    missing = []
    provider = str(cfg.get("email_provider") or "").strip().lower()
    if provider == "cloudflare" and not str(cfg.get("cloudflare_api_base") or "").strip():
        missing.append("cloudflare_api_base")
    if provider == "cloudmail" and not str(cfg.get("cloudmail_url") or "").strip():
        missing.append("cloudmail_url")
    if provider == "duckmail" and not str(cfg.get("duckmail_api_key") or "").strip():
        missing.append("duckmail_api_key")
    if provider == "yyds" and not (str(cfg.get("yyds_api_key") or "").strip() or str(cfg.get("yyds_jwt") or "").strip()):
        missing.append("yyds_api_key/yyds_jwt")
    if not str(cfg.get("defaultDomains") or "").strip() and provider in ("cloudflare", "cloudmail", "yyds"):
        missing.append("defaultDomains")
    if cfg.get("cpa_auto_add"):
        if not str(cfg.get("cpa_auth_dir") or "").strip() and not remote:
            missing.append("cpa_auth_dir 或 cpa_remote_url")
        if remote and not str(cfg.get("cpa_management_key") or "").strip():
            missing.append("cpa_management_key")

    external_ok = not local_markers and not missing
    return dict(
        external_mode=bool(cfg.get("external_mode", True)),
        external_ok=external_ok,
        local_dependencies=local_markers,
        missing_fields=missing,
        config_file=str(CONFIG_FILE),
        data_dir=str(DATA_DIR),
        cpa_dir=str(CPA_DIR),
        guidance=(
            "外接模式：请通过表单填写邮箱服务、代理、远程 CPA。不要依赖本机 127.0.0.1 / cliproxy hotload。"
            if not external_ok
            else "外接模式就绪：未检测到本机硬依赖。"
        ),
    )


@app.route("/api/status")
def api_status():
    cfg = load_config()
    report = _external_dependency_report(cfg)
    return jsonify(
        dict(
            running=_is_running(),
            stats=_stats,
            accounts_count=len(read_accounts()),
            cpa_count=cpa_file_count(),
            external=report,
        )
    )


@app.route("/api/external_check", methods=["GET"])
def api_external_check():
    return jsonify(_external_dependency_report())


@app.route("/api/accounts")
def api_accounts():
    return jsonify(dict(accounts=read_accounts()))


@app.route("/api/config", methods=["GET"])
def api_config_get():
    return jsonify(load_config())


@app.route("/api/config", methods=["POST"])
def api_config_post():
    data = request.get_json(force=True) or {}
    # normalize worker fields
    if "register_workers" in data:
        try:
            data["register_workers"] = max(1, min(int(data["register_workers"] or 1), 8))
        except Exception:
            data["register_workers"] = 1
        data["register_threads"] = data["register_workers"]
    elif "register_threads" in data:
        try:
            data["register_threads"] = max(1, min(int(data["register_threads"] or 1), 8))
        except Exception:
            data["register_threads"] = 1
        data["register_workers"] = data["register_threads"]

    # External-mode normalizations: keep workflow portable across hosts.
    data.setdefault("external_mode", True)
    for k in ("proxy", "cpa_proxy", "cpa_hotload_dir", "cpa_remote_url", "cpa_management_key"):
        if k in data and data[k] is None:
            data[k] = ""
    if "cpa_auth_dir" in data:
        data["cpa_auth_dir"] = str(data.get("cpa_auth_dir") or "").strip() or str(CPA_DIR)
    if "cpa_copy_to_hotload" in data:
        # accept bool/string
        v = data.get("cpa_copy_to_hotload")
        if isinstance(v, str):
            data["cpa_copy_to_hotload"] = v.strip().lower() in ("1", "true", "yes", "on")
        else:
            data["cpa_copy_to_hotload"] = bool(v)
        if data["cpa_copy_to_hotload"] and not str(data.get("cpa_hotload_dir") or "").strip():
            # refuse silent host fallback
            data["cpa_copy_to_hotload"] = False
    cfg = save_config(data)
    report = _external_dependency_report(cfg)
    return jsonify(dict(ok=True, config=cfg, external=report))


@app.route("/api/start", methods=["POST"])
def api_start():
    if _is_running():
        return jsonify(dict(ok=False, error="已有任务在运行")), 409
    data = request.get_json(force=True) or {}
    try:
        extra = int(data.get("count") or data.get("extra") or 1)
    except Exception:
        extra = 1
    try:
        workers = int(data.get("workers") or data.get("threads") or load_config().get("register_workers") or 1)
    except Exception:
        workers = 1
    # Keep in sync with frontend input max (templates/index.html).
    requested_extra = extra
    requested_workers = workers
    extra = max(1, min(extra, 200))
    workers = max(1, min(workers, 10, extra))
    if requested_extra != extra or requested_workers != workers:
        _broadcast(dict(
            ts=time.strftime("%H:%M:%S"),
            msg=f"[Web] 参数已调整: 数量 {requested_extra}->{extra}, 并发 {requested_workers}->{workers}",
        ))
    t = threading.Thread(target=_run_registration, args=(extra, workers), daemon=True)
    t.start()
    return jsonify(dict(
        ok=True,
        count=extra,
        workers=workers,
        requested_count=requested_extra,
        requested_workers=requested_workers,
        capped=(requested_extra != extra or requested_workers != workers),
    ))


@app.route("/api/stop", methods=["POST"])
def api_stop():
    _cancel_event.set()
    ctrl = _runner_controller
    if ctrl is not None:
        try:
            ctrl.stop()
        except Exception:
            pass
    # 停止时立刻清理残留注册浏览器（不等待 worker 优雅退出）
    cleaned = 0
    try:
        from browser_session import (
            cleanup_orphan_register_browsers,
            cleanup_browser_temp_dirs,
            reap_zombie_children,
        )
        cleaned = cleanup_orphan_register_browsers(force=True) or 0
        tmp = cleanup_browser_temp_dirs(force=True, max_age_sec=60)
        z = reap_zombie_children()
        _broadcast(dict(
            ts=time.strftime("%H:%M:%S"),
            msg=(
                f"[Web] 停止清理: browser_dirs={cleaned} "
                f"tmp_dirs={tmp.get('removed_dirs', 0)} zombies={z}"
            ),
        ))
    except Exception as exc:
        _broadcast(dict(ts=time.strftime("%H:%M:%S"), msg=f"[Web] 停止清理浏览器失败: {exc}"))
    return jsonify(dict(ok=True, cleaned_browser_dirs=cleaned))


@app.route("/api/cpa/mint_single", methods=["POST"])
def api_cpa_mint_single():
    """Convert existing SSO cookie into CPA auth via pure HTTP OIDC."""
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip()
    sso = (data.get("sso") or "").strip()
    if not sso:
        return jsonify(dict(ok=False, error="缺少 sso")), 400
    try:
        import grok_register_ttk as reg
        import sso_to_auth_json as s2cpa

        reg.load_config()
        cfg = load_config()
        for k, v in cfg.items():
            reg.config[k] = v
        auth_dir = Path(str(reg.config.get("cpa_auth_dir") or CPA_DIR))
        auth_dir.mkdir(parents=True, exist_ok=True)

        def log_cb(msg):
            _broadcast(dict(ts=time.strftime("%H:%M:%S"), msg=f"[CPA-Mint] [{email or 'sso'}] {msg}"))

        ok = reg.add_sso_to_cpa(sso, email=email, log_callback=log_cb)
        if ok:
            _hotload_copy_if_enabled(reg.config, log_cb)
            return jsonify(dict(ok=True))
        return jsonify(dict(ok=False, error="SSO→CPA 失败，请看日志")), 500
    except Exception as e:
        return jsonify(dict(ok=False, error=str(e))), 500


@app.route("/api/cpa/probe", methods=["POST"])
def api_cpa_probe():
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip()
    if not email:
        return jsonify(dict(ok=False, error="邮箱参数缺失")), 400

    matched = None
    direct = CPA_DIR / f"xai-{email}.json"
    if direct.exists():
        matched = direct
    else:
        for cand in CPA_DIR.glob("xai-*.json"):
            try:
                obj = json.loads(cand.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(obj.get("email") or "").lower() == email.lower():
                matched = cand
                break
    if not matched:
        return jsonify(dict(ok=False, error="对应的 CPA OIDC 凭证文件不存在")), 404

    try:
        import sso_to_auth_json as s2cpa

        cpa_data = json.loads(matched.read_text(encoding="utf-8"))
        cfg = load_config()
        proxy = cfg.get("proxy") or None
        t0 = time.time()
        res = s2cpa.probe_cpa_record(cpa_data, proxy=proxy)
        if isinstance(res, dict):
            res["elapsed"] = round(time.time() - t0, 2)
            res["file"] = matched.name
            return jsonify(res)
        return jsonify(dict(ok=bool(res), elapsed=round(time.time() - t0, 2), file=matched.name))
    except Exception as e:
        return jsonify(dict(ok=False, error=str(e))), 500


@app.route("/api/test/mail", methods=["POST"])
def api_test_mail():
    try:
        import connectivity as conn
        import grok_register_ttk as reg
        from curl_cffi import requests as cffi_requests

        reg.load_config()
        cfg = load_config()
        for k, v in cfg.items():
            reg.config[k] = v
        provider = (cfg.get("email_provider") or "cloudflare").strip().lower()
        t0 = time.time()

        def http_get(url, headers=None, params=None, timeout=10, proxies=None):
            return cffi_requests.get(url, headers=headers or {}, params=params, timeout=timeout, proxies=proxies, impersonate="chrome")

        def http_post(url, headers=None, json=None, data=None, timeout=10, proxies=None):
            return cffi_requests.post(url, headers=headers or {}, json=json, data=data, timeout=timeout, proxies=proxies, impersonate="chrome")

        name, ok, detail = conn.check_email_api(provider, cfg, http_get, http_post)
        return jsonify(dict(ok=ok, provider=provider, name=name, detail=detail, elapsed=round(time.time() - t0, 2)))
    except Exception as e:
        return jsonify(dict(ok=False, error=str(e))), 500


@app.route("/api/test/sys_check", methods=["GET"])
def api_test_sys_check():
    import platform
    import shutil

    chrome_path = ""
    for cand in (
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
        "/usr/local/bin/chromium",
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    ):
        if os.path.exists(cand):
            chrome_path = cand
            break
    if not chrome_path:
        chrome_path = shutil.which("chrome") or shutil.which("chromium") or "未检测到系统Chrome"
    return jsonify(
        dict(
            python_version=sys.version,
            platform=platform.platform(),
            chrome_path=chrome_path,
            workspace=str(BASE_DIR),
            display=os.environ.get("DISPLAY", ""),
            upstream="Git-creat7/grokRegister-cpa",
        )
    )


@app.route("/api/test/connectivity", methods=["GET", "POST"])
def api_test_connectivity():
    try:
        import connectivity as conn
        import grok_register_ttk as reg
        from curl_cffi import requests as cffi_requests

        reg.load_config()
        cfg = load_config()
        for k, v in cfg.items():
            reg.config[k] = v

        def http_get(url, headers=None, params=None, timeout=10, proxies=None):
            return cffi_requests.get(url, headers=headers or {}, params=params, timeout=timeout, proxies=proxies, impersonate="chrome")

        def http_post(url, headers=None, json=None, data=None, timeout=10, proxies=None):
            return cffi_requests.post(url, headers=headers or {}, json=json, data=data, timeout=timeout, proxies=proxies, impersonate="chrome")

        results = conn.run_connectivity_checks(cfg, http_get, http_post)
        items = [dict(name=n, ok=ok, detail=d) for n, ok, d in results]
        all_ok = all(ok for _, ok, _ in results)
        return jsonify(dict(ok=all_ok, result=items, text=conn.format_check_results(results)))
    except Exception as e:
        return jsonify(dict(ok=False, error=str(e))), 500


@app.route("/api/logs/stream")
def api_logs_stream():
    q = _subscribe()

    def generate():
        with _log_lock:
            for entry in list(_log_history[-50:]):
                yield "data: " + json.dumps(entry, ensure_ascii=False) + "\n\n"
        try:
            while True:
                try:
                    entry = q.get(timeout=30)
                    yield "data: " + json.dumps(entry, ensure_ascii=False) + "\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            _unsubscribe(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    CPA_DIR.mkdir(parents=True, exist_ok=True)
    # bind only localhost; nginx reverse proxies /grok/
    app.run(host="127.0.0.1", port=18425, threaded=True)
