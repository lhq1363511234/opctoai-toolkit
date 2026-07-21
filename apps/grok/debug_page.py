#!/usr/bin/env python3
import json
import tempfile
import time
import traceback

from DrissionPage import Chromium, ChromiumOptions


def main():
    print("start", flush=True)
    opts = ChromiumOptions()
    opts.set_browser_path("/usr/local/bin/chromium")
    opts.auto_port()
    ud = tempfile.mkdtemp(prefix="grok-dp-")
    opts.set_user_data_path(ud)
    for f in [
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--window-size=1280,900",
        "--remote-allow-origins=*",
        "--proxy-server=http://127.0.0.1:7898",
    ]:
        opts.set_argument(f)
    print("ud", ud, flush=True)
    browser = Chromium(opts)
    print("connected", flush=True)
    tab = browser.latest_tab
    tab.get("https://accounts.x.ai/sign-up?redirect=grok-com")
    time.sleep(6)
    print("url", tab.url, flush=True)
    print("title", tab.title, flush=True)
    text = tab.run_js('return document.body?document.body.innerText.slice(0,2000):""')
    print("TEXT:", text, flush=True)
    open("/tmp/grok-debug-signup.html", "w").write(tab.html or "")
    try:
        tab.get_screenshot(path="/tmp/grok-debug-signup.png", full_page=True)
    except Exception as e:
        print("shot", e, flush=True)
    info = tab.run_js(
        """return [...document.querySelectorAll('input,button,a,div[role=button]')].slice(0,100).map(n=>({
          tag:n.tagName,type:n.type||'',name:n.name||'',test:n.getAttribute('data-testid')||'',
          text:(n.innerText||n.textContent||'').replace(/\\s+/g,' ').slice(0,80),
          vis:!!(n.offsetWidth||n.offsetHeight)
        }));"""
    )
    print(json.dumps(info, ensure_ascii=False, indent=2)[:6000], flush=True)
    clicked = tab.run_js(
        """const nodes=[...document.querySelectorAll('button,a,div[role=button],span')];
        const b=nodes.find(n=>/邮箱|email|Email|mail|Sign up with email/i.test((n.innerText||n.textContent||'')));
        if(!b) return {ok:false, candidates:nodes.map(n=>(n.innerText||'').replace(/\\s+/g,' ').slice(0,50)).filter(Boolean).slice(0,40)};
        b.click();
        return {ok:true,text:(b.innerText||'').replace(/\\s+/g,' ').slice(0,80)};"""
    )
    print("click", clicked, flush=True)
    time.sleep(5)
    print("url2", tab.url, flush=True)
    print("TEXT2:", tab.run_js('return document.body?document.body.innerText.slice(0,1500):""'), flush=True)
    print(
        "inputs",
        tab.run_js(
            """return [...document.querySelectorAll('input')].map(n=>({
              type:n.type,name:n.name,test:n.getAttribute('data-testid'),ph:n.placeholder,id:n.id,
              vis:!!(n.offsetWidth||n.offsetHeight)
            }));"""
        ),
        flush=True,
    )
    try:
        tab.get_screenshot(path="/tmp/grok-debug-after-email-btn.png", full_page=True)
    except Exception as e:
        print("shot2", e, flush=True)
    browser.quit()
    print("done", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
