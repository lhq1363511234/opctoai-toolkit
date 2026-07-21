# -*- coding: utf-8 -*-
"""浏览器会话管理（线程本地 browser/page）。"""
from __future__ import annotations

import gc
import shutil
import os
import subprocess
import socket
import tempfile
import threading
import time
import uuid
from typing import Callable, Optional, Tuple

from DrissionPage import Chromium, ChromiumOptions

_tls = threading.local()
_get_proxy: Optional[Callable[[], dict]] = None
_is_debug: Optional[Callable[[], bool]] = None
_extension_path: str = ""
_start_fail_lock = threading.Lock()
_start_fail_streak = 0
_start_fail_threshold = 3


def configure(get_proxies=None, is_debug=None, extension_path=""):
    global _get_proxy, _is_debug, _extension_path
    _get_proxy = get_proxies
    _is_debug = is_debug
    _extension_path = extension_path or ""


def get_start_fail_streak() -> int:
    with _start_fail_lock:
        return _start_fail_streak


def _note_start_success():
    global _start_fail_streak
    with _start_fail_lock:
        _start_fail_streak = 0


def _note_start_failure():
    global _start_fail_streak
    with _start_fail_lock:
        _start_fail_streak += 1
        return _start_fail_streak


def _proxies() -> dict:
    if _get_proxy:
        return _get_proxy() or {}
    return {}


def _debug() -> bool:
    return bool(_is_debug()) if _is_debug else False


def active_browser():
    return getattr(_tls, "browser", None)


def active_page():
    return getattr(_tls, "page", None)


def set_browser_session(browser_obj=None, page_obj=None):
    _tls.browser = browser_obj
    _tls.page = page_obj


class _SessionProxy:
    __slots__ = ("_key",)

    def __init__(self, key):
        self._key = key

    def _obj(self):
        return getattr(_tls, self._key, None)

    def __bool__(self):
        return self._obj() is not None

    def __eq__(self, other):
        return self._obj() is other

    def __ne__(self, other):
        return self._obj() is not other

    def __getattr__(self, name):
        obj = self._obj()
        if obj is None:
            raise AttributeError(f"{self._key} is not started")
        return getattr(obj, name)


browser = _SessionProxy("browser")
page = _SessionProxy("page")


def _free_local_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        try:
            sock.close()
        except Exception:
            pass


def create_browser_options(unique_profile=True):
    """创建 ChromiumOptions。

    注意：DrissionPage 下 set_user_data_path 会破坏 auto_port() 的 address
    （触发 not enough values to unpack）。并发隔离应使用：
    set_local_port(空闲端口) + set_user_data_path(独立目录)。

    服务器/Xvfb 环境额外强制：
    - 指定 chromium 可执行文件
    - --no-sandbox / --disable-dev-shm-usage
    - DISPLAY 未设置时回退 :99
    """
    # Xvfb fallback for headless servers
    if not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = ":99"

    options = ChromiumOptions()
    options.set_timeouts(base=1.5)

    # Prefer the known chromium symlink used by this host.
    for chrome_path in (
        "/usr/local/bin/chromium",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ):
        if os.path.exists(chrome_path):
            try:
                options.set_browser_path(chrome_path)
            except Exception:
                try:
                    options.set_paths(browser_path=chrome_path)
                except Exception:
                    pass
            break

    proxies = _proxies()
    proxy = str(proxies.get("https") or proxies.get("http") or "").strip()
    if proxy:
        options.set_proxy(proxy)

    # Linux server flags (critical for Playwright-bundled chrome under root)
    for arg in (
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-features=TranslateUI,BlinkGenPropertyTrees",
        "--window-size=1280,900",
    ):
        try:
            options.set_argument(arg)
        except Exception:
            pass

    # Keep headed on Xvfb; only force headless if explicitly requested.
    if str(os.environ.get("GROK_FORCE_HEADLESS", "")).strip() in {"1", "true", "yes"}:
        try:
            options.set_argument("--headless=new")
        except Exception:
            pass

    if unique_profile:
        profile_dir = os.path.join(
            tempfile.gettempdir(),
            "grok-register-chrome",
            f"{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex[:8]}",
        )
        os.makedirs(profile_dir, exist_ok=True)
        port = _free_local_port()
        options.set_local_port(port)
        options.set_user_data_path(profile_dir)
        _tls.profile_dir = profile_dir
        _tls.debug_port = port
    else:
        options.auto_port()
    if _extension_path and os.path.exists(_extension_path):
        options.add_extension(_extension_path)
    return options


def start_browser(log_callback=None) -> Tuple[object, object]:
    last_exc = None
    for attempt in range(1, 5):
        try:
            browser_obj = Chromium(create_browser_options(unique_profile=True))
            tabs = browser_obj.get_tabs()
            page_obj = tabs[-1] if tabs else browser_obj.new_tab()
            set_browser_session(browser_obj, page_obj)
            _note_start_success()
            profile = getattr(_tls, "profile_dir", None) or getattr(browser_obj, "user_data_path", None)
            if log_callback and profile:
                log_callback(f"[Debug] 当前浏览器资料目录: {profile}")
            if log_callback and attempt > 1:
                log_callback(f"[*] 浏览器第 {attempt} 次启动成功")
            return browser_obj, page_obj
        except Exception as exc:
            last_exc = exc
            streak = _note_start_failure()
            if log_callback:
                log_callback(f"[Debug] 浏览器启动失败(第{attempt}/4次, 连续失败{streak}): {exc}")
            try:
                cur = active_browser()
                if cur is not None:
                    cur.quit(del_data=True)
            except Exception:
                pass
            set_browser_session(None, None)
            time.sleep(min(1.5 * attempt, 4))
    raise Exception(f"浏览器启动失败，已重试4次: {last_exc}")


def stop_browser(force=False):
    if _debug() and not force:
        return
    current = active_browser()
    profile = getattr(_tls, "profile_dir", None)
    set_browser_session(None, None)
    try:
        if hasattr(_tls, "profile_dir"):
            delattr(_tls, "profile_dir")
    except Exception:
        pass
    if current is not None:
        try:
            current.quit(del_data=True)
        except BaseException:
            pass
    # best-effort kill leftover chrome for this profile
    if profile:
        _kill_chrome_by_profile(profile)


def register_chrome_root() -> str:
    return os.path.join(tempfile.gettempdir(), "grok-register-chrome")


def _iter_pids_cmdline_contains(substr: str):
    """Yield browser PIDs whose /proc/<pid>/cmdline contains substr.

    严格只匹配真实 chrome/chromium 浏览器进程，避免误杀包含同名字符串的 shell/python。
    """
    needle = (substr or "").encode()
    if not needle:
        return
    my_pid = os.getpid()
    my_ppid = os.getppid()
    try:
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            pid = int(name)
            if pid in (my_pid, my_ppid, 1):
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    cmd = f.read()
            except Exception:
                continue
            if needle not in cmd:
                continue
            # argv0 / exe must look like a real browser binary
            try:
                exe = os.readlink(f"/proc/{pid}/exe")
            except Exception:
                exe = ""
            exe_l = (exe or "").lower()
            arg0 = cmd.split(b"\x00", 1)[0].decode("utf-8", "ignore").lower()
            joined = cmd.replace(b"\x00", b" ").decode("utf-8", "ignore").lower()
            is_browser_bin = (
                "chrome" in exe_l
                or "chromium" in exe_l
                or arg0.endswith("chrome")
                or arg0.endswith("chromium")
                or "/chrome" in arg0
                or "/chromium" in arg0
                or arg0.endswith("chrome-wrapper")
            )
            # require user-data-dir style arg when possible
            has_profile_arg = ("--user-data-dir=" in joined) or ("grok-register-chrome" in joined)
            if not is_browser_bin:
                continue
            if substr and substr not in joined and needle not in cmd:
                continue
            if not has_profile_arg and "grok-register-chrome" not in joined:
                continue
            yield pid
    except Exception:
        return


def reap_zombie_children(max_rounds: int = 64) -> int:
    """Non-blocking waitpid loop so killed chrome children do not stay <defunct>.

    In the container the web process is often PID1; without wait(), SIGKILL'd
    chromium/crashpad children become zombies until container restart.
    """
    import errno as _errno
    reaped = 0
    for _ in range(max(1, int(max_rounds or 1))):
        progressed = False
        while True:
            try:
                pid, _status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            except OSError as exc:
                if getattr(exc, "errno", None) in {_errno.ECHILD, getattr(_errno, "ECHILD", 10)}:
                    break
                break
            if pid <= 0:
                break
            reaped += 1
            progressed = True
        if not progressed:
            break
    return reaped


def _kill_pids(pids):
    import signal as _signal
    pids = sorted(set(int(p) for p in pids if int(p) > 1))
    for pid in pids:
        try:
            os.kill(pid, _signal.SIGTERM)
        except Exception:
            pass
    # brief grace, then SIGKILL leftovers
    time.sleep(0.4)
    for pid in pids:
        try:
            os.kill(pid, 0)
        except Exception:
            continue
        try:
            os.kill(pid, _signal.SIGKILL)
        except Exception:
            pass
    # Always try to reap; parent may be this process (container PID1 case).
    reap_zombie_children()


def _kill_chrome_by_profile(profile: str):
    profile = os.path.abspath(profile or "")
    if not profile:
        return
    _kill_pids(list(_iter_pids_cmdline_contains(profile)))
    try:
        shutil.rmtree(profile, ignore_errors=True)
    except Exception:
        pass


def _iter_orphan_browser_related_pids(active_profile_markers=None):
    """Yield chrome/chromium/crashpad PIDs that look like register leftovers.

    When *active_profile_markers* is provided, PIDs whose cmdline contains any
    marker (live worker profile path) are skipped so reuse browsers are kept.
    """
    markers = [str(m) for m in (active_profile_markers or []) if m]
    my_pid = os.getpid()
    my_ppid = os.getppid()
    try:
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            pid = int(name)
            if pid in (my_pid, my_ppid, 1):
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    cmd = f.read()
            except Exception:
                continue
            if not cmd:
                continue
            joined = cmd.replace(b"\x00", b" ").decode("utf-8", "ignore")
            joined_l = joined.lower()
            try:
                exe = os.readlink(f"/proc/{pid}/exe")
            except Exception:
                exe = ""
            exe_l = (exe or "").lower()
            arg0 = cmd.split(b"\x00", 1)[0].decode("utf-8", "ignore").lower()

            is_browser_bin = (
                "chrome" in exe_l
                or "chromium" in exe_l
                or "chrome" in arg0
                or "chromium" in arg0
            )
            is_crashpad = (
                "chrome_crashpad" in joined_l
                or "crashpad_handler" in joined_l
                or "crashpad" in arg0
            )
            if not (is_browser_bin or is_crashpad):
                continue

            # Keep live worker browsers (profile markers still in use).
            if markers and any(m in joined for m in markers):
                continue

            # Orphan candidates: register profiles, Drission temp ports, or
            # crashpad without a matching live profile marker.
            is_register_profile = (
                "grok-register-chrome" in joined
                or "/tmp/drissionpage" in joined_l
                or "autoportdata" in joined_l
            )
            if is_register_profile or is_crashpad:
                yield pid
    except Exception:
        return


def cleanup_browser_temp_dirs(
    log_callback=None,
    max_age_sec: int = 1800,
    force: bool = False,
) -> dict:
    """Remove stale chromium/Drission/register temp dirs under /tmp.

    Does not touch profiles younger than *max_age_sec* unless force=True.
    """
    tmp = tempfile.gettempdir()
    now = time.time()
    removed_dirs = 0
    removed_files = 0
    patterns_dirs = (
        "grok-register-chrome",
        "DrissionPage",
        "drissionpage",
    )
    # Always consider register root
    roots = []
    for name in patterns_dirs:
        p = os.path.join(tmp, name)
        if os.path.isdir(p):
            roots.append(p)
    # Chromium system temp blobs
    try:
        for name in os.listdir(tmp):
            if name.startswith("org.chromium.") or name.startswith(".com.google.Chrome") or name.startswith("chrome_"):
                roots.append(os.path.join(tmp, name))
            if name.startswith(".X") and name.endswith("-lock"):
                # stale Xvfb locks only when force or old
                roots.append(os.path.join(tmp, name))
    except Exception:
        pass

    for path in roots:
        try:
            try:
                age = now - os.path.getmtime(path)
            except Exception:
                age = 0
            if not force and age < max(60, int(max_age_sec or 0)):
                continue
            if os.path.isdir(path):
                # If this is the register root, delete child profiles by age
                base = os.path.basename(path.rstrip(os.sep))
                if base in {"grok-register-chrome", "DrissionPage", "drissionpage"} and not force:
                    try:
                        for child in os.listdir(path):
                            cpath = os.path.join(path, child)
                            try:
                                cage = now - os.path.getmtime(cpath)
                            except Exception:
                                cage = 0
                            if cage < max(60, int(max_age_sec or 0)):
                                continue
                            if os.path.isdir(cpath):
                                shutil.rmtree(cpath, ignore_errors=True)
                                removed_dirs += 1
                            else:
                                try:
                                    os.remove(cpath)
                                    removed_files += 1
                                except Exception:
                                    pass
                    except Exception:
                        pass
                else:
                    if os.path.isdir(path):
                        shutil.rmtree(path, ignore_errors=True)
                        removed_dirs += 1
                    elif os.path.isfile(path) or os.path.islink(path):
                        try:
                            os.remove(path)
                            removed_files += 1
                        except Exception:
                            pass
            elif os.path.isfile(path) or os.path.islink(path):
                try:
                    os.remove(path)
                    removed_files += 1
                except Exception:
                    pass
        except Exception:
            pass

    stats = {"removed_dirs": removed_dirs, "removed_files": removed_files}
    if log_callback and (removed_dirs or removed_files):
        log_callback(
            f"[*] 临时目录清理: dirs={removed_dirs} files={removed_files} force={bool(force)}"
        )
    return stats


def cleanup_orphan_register_browsers(log_callback=None, force: bool = True) -> int:
    """关闭/清理注册浏览器残留（profile + crashpad + 临时目录）。

    多线程停止时 thread-local quit 可能漏掉孤儿进程，这里做全局兜底。
    返回清理到的 profile 目录数（估算）。

    force=True: 结束任务/停止时使用，可杀全部注册相关浏览器并强清 tmp。
    force=False: 仅清理明显孤儿与过期 tmp，尽量不碰仍在复用的 worker 浏览器。
    """
    if _debug() and not force:
        return 0

    root = register_chrome_root()
    # Live profile markers: keep when not force (worker reuse path).
    active_markers = []
    if not force and os.path.isdir(root):
        try:
            for name in os.listdir(root):
                p = os.path.join(root, name)
                if os.path.isdir(p):
                    # recently touched profiles are treated as possibly live
                    try:
                        if time.time() - os.path.getmtime(p) < 600:
                            active_markers.append(p)
                    except Exception:
                        active_markers.append(p)
        except Exception:
            pass
    # also keep current thread profile
    cur_profile = getattr(_tls, "profile_dir", None)
    if cur_profile:
        active_markers.append(str(cur_profile))

    # kill chrome processes bound to register profiles even if root already gone
    _kill_pids(list(_iter_pids_cmdline_contains("grok-register-chrome")))
    if root:
        _kill_pids(list(_iter_pids_cmdline_contains(root)))
    # crashpad / Drission leftovers (skip live markers unless force)
    orphan_pids = list(
        _iter_orphan_browser_related_pids(None if force else active_markers)
    )
    if orphan_pids:
        _kill_pids(orphan_pids)

    count = 0
    if os.path.isdir(root):
        try:
            for name in os.listdir(root):
                path = os.path.join(root, name)
                if not force and path in active_markers:
                    continue
                if os.path.isdir(path):
                    count += 1
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    try:
                        os.remove(path)
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            if force or not os.listdir(root):
                os.rmdir(root)
        except Exception:
            pass

    # tmp sweep: force on stop/end; otherwise age-based
    tmp_stats = cleanup_browser_temp_dirs(
        log_callback=log_callback,
        max_age_sec=120 if force else 1800,
        force=bool(force),
    )
    reaped = reap_zombie_children()
    if log_callback:
        log_callback(
            f"[*] 已清理残留注册浏览器目录: {count}; "
            f"tmp_dirs={tmp_stats.get('removed_dirs', 0)} "
            f"reaped_zombies={reaped}"
        )
    return count


_reaper_thread = None
_reaper_stop = threading.Event()


def start_background_reaper(interval_sec: float = 45.0, log_callback=None):
    """Daemon reaper: waitpid zombies + age-based tmp cleanup; never force-kills live workers."""
    global _reaper_thread
    if _reaper_thread is not None and _reaper_thread.is_alive():
        return _reaper_thread

    def _loop():
        while not _reaper_stop.is_set():
            try:
                reaped = reap_zombie_children()
                # soft cleanup only
                cleanup_browser_temp_dirs(max_age_sec=1800, force=False)
                # kill clearly orphan crashpads with no active young profiles
                soft_pids = list(_iter_orphan_browser_related_pids(active_profile_markers=[]))
                # Only kill crashpad-looking orphans in soft mode to avoid killing reuse browsers
                crash_only = []
                for pid in soft_pids:
                    try:
                        with open(f"/proc/{pid}/cmdline", "rb") as f:
                            cmd = f.read().replace(b"\x00", b" ").decode("utf-8", "ignore").lower()
                    except Exception:
                        continue
                    if "crashpad" in cmd:
                        # skip if any young register profile exists (workers running)
                        root = register_chrome_root()
                        young = False
                        if os.path.isdir(root):
                            try:
                                for name in os.listdir(root):
                                    p = os.path.join(root, name)
                                    try:
                                        if time.time() - os.path.getmtime(p) < 900:
                                            young = True
                                            break
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                        if not young:
                            crash_only.append(pid)
                if crash_only:
                    _kill_pids(crash_only)
                if log_callback and reaped:
                    log_callback(f"[reaper] reaped_zombies={reaped}")
            except Exception:
                pass
            _reaper_stop.wait(max(15.0, float(interval_sec or 45.0)))

    _reaper_stop.clear()
    _reaper_thread = threading.Thread(target=_loop, name="grok-browser-reaper", daemon=True)
    _reaper_thread.start()
    return _reaper_thread


def stop_background_reaper():
    _reaper_stop.set()



def restart_browser(log_callback=None):
    stop_browser(force=True)
    return start_browser(log_callback=log_callback)


def cleanup_runtime_memory(log_callback=None, reason="定期清理"):
    try:
        if _debug():
            if log_callback:
                log_callback(f"[*] 调试模式：保留浏览器（{reason}）")
            collected = gc.collect()
            if log_callback:
                log_callback(f"[*] Python GC 已回收对象数: {collected}")
            return
        if log_callback:
            log_callback(f"[*] {reason}: 关闭浏览器并清理内存")
        stop_browser(force=True)
        cleanup_orphan_register_browsers(log_callback=log_callback, force=True)
        reap_zombie_children()
        collected = gc.collect()
        if log_callback:
            log_callback(f"[*] Python GC 已回收对象数: {collected}")
    except BaseException:
        try:
            if not _debug():
                stop_browser(force=True)
                cleanup_orphan_register_browsers(force=True)
        except BaseException:
            pass


def refresh_active_page():
    if active_browser() is None:
        restart_browser()
    try:
        browser_obj = active_browser()
        tabs = browser_obj.get_tabs()
        page_obj = tabs[-1] if tabs else browser_obj.new_tab()
        set_browser_session(browser_obj, page_obj)
    except Exception:
        restart_browser()
    return page


def extract_cf_clearance_and_ua(log_callback=None, ensure_grok=True):
    """提取 grok.com 域 cf_clearance + UA。"""
    cf_clearance = ""
    user_agent = ""
    try:
        active = refresh_active_page()
        if active is None:
            return "", ""

        def _read_cf_and_ua(page_obj, grok_only=False):
            clearance = ""
            ua_text = ""
            cookies = page_obj.cookies(all_domains=True, all_info=True) or []
            for item in cookies:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    value = str(item.get("value", "")).strip()
                    domain = str(item.get("domain", "")).strip().lower()
                else:
                    name = str(getattr(item, "name", "")).strip()
                    value = str(getattr(item, "value", "")).strip()
                    domain = str(getattr(item, "domain", "")).strip().lower()
                if name != "cf_clearance" or not value:
                    continue
                if grok_only and "grok.com" not in domain:
                    continue
                if "grok.com" in domain:
                    clearance = value
                    break
                if not clearance and not grok_only:
                    clearance = value
            try:
                ua = page_obj.run_js("return navigator.userAgent;")
                if ua:
                    ua_text = str(ua).strip()
            except Exception:
                pass
            return clearance, ua_text

        def _page_passed_cf(page_obj):
            try:
                title = str(page_obj.run_js("return document.title || '';") or "").lower()
                body = str(
                    page_obj.run_js(
                        "return (document.body && (document.body.innerText||'')) || '';"
                    )
                    or ""
                ).lower()
                if "just a moment" in title or "just a moment" in body[:200]:
                    return False
                if "checking your browser" in body[:300]:
                    return False
                return True
            except Exception:
                return False

        cf_clearance, user_agent = _read_cf_and_ua(active, grok_only=True)
        if ensure_grok and not cf_clearance:
            if log_callback:
                log_callback("[*] 未找到 grok.com 的 cf_clearance，打开 grok.com 过盾...")
            try:
                active.get("https://grok.com/")
                try:
                    active.wait.doc_loaded()
                except Exception:
                    pass
                time.sleep(2)
                for _ in range(20):
                    if _page_passed_cf(active):
                        cf_clearance, user_agent = _read_cf_and_ua(active, grok_only=True)
                        if cf_clearance:
                            break
                    time.sleep(1.0)
                if log_callback:
                    if cf_clearance:
                        log_callback("[*] 已取得 grok.com 的 cf_clearance")
                    else:
                        log_callback(
                            "[!] 打开 grok.com 后仍无有效 cf_clearance（页面可能仍卡在 Just a moment）"
                        )
            except Exception as nav_exc:
                if log_callback:
                    log_callback(f"[Debug] 打开 grok.com 取 cf_clearance 失败: {nav_exc}")
                cf_clearance, user_agent = _read_cf_and_ua(active, grok_only=True)
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] 提取 cf_clearance 失败: {exc}")
    return cf_clearance, user_agent
