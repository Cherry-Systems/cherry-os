#!/usr/bin/env python3
"""Tests Cherry Move for macOS on a real Mac (run by mac-workflow.yml on GitHub's macOS machines).
  mac_test.py CherryMove.dmg OUT_DIR

1. Opens the DMG as if it had just been downloaded (quarantined), checks what's in it, drags the app to
   Applications and screenshots what Gatekeeper says when it's opened.
2. Clears the quarantine (what "Open Anyway" does) and checks the app opens in its own window.
3. In that window, with test files from make_files.py in a separate home folder: opening it again brings the
   first one to the front, closing it while files are being moved asks first, a whole move, then "Print code"
   and "Save code as a file…" through the system's print and save boxes, and quitting from the menu.
4. Clicks through a whole move in Safari (WebDriver; the same page and web engine as the window) onto two
   128 MB disk images standing in for USB sticks. The sticks and hashes end up in OUT_DIR for
   restore_sticks.sh to check on Linux.
Clicking in the window needs System Events (accessibility); where that's not allowed, the test says so and
does the same through the app's API."""
import faulthandler
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import (ElementClickInterceptedException, ElementNotInteractableException,
                                        StaleElementReferenceException, TimeoutException)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait

DMG, OUT = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
HERE = Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)
report: dict = {"machine": platform.machine(), "macos": platform.mac_ver()[0], "dmg": DMG.name, "checks": {}}
faulthandler.dump_traceback_later(25 * 60, exit=True)  # shows where it got stuck instead of hanging the job
APP = Path("/Applications/Cherry Move.app")
EXE = APP / "Contents/MacOS/CherryMove"
VOLUME = Path("/Volumes/Cherry Move")


def sh(*cmd, check=False, timeout=120) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out, failed = (r.stdout + r.stderr).strip(), r.returncode != 0
    except subprocess.TimeoutExpired:
        out, failed = f"(still running after {timeout} s)", True
    print(f"$ {' '.join(map(str, cmd))}\n{out}", flush=True)
    if check and failed:
        raise SystemExit(f"failed: {cmd}")
    return out


def check(ok, what) -> bool:
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    report["checks"][what] = bool(ok)
    return bool(ok)


def wait(cond, timeout=60.0):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.5)
    return None


def launch():
    """Double-click: `open` without waiting on its output (Gatekeeper's prompt keeps it from ever closing)."""
    print(f"$ open {APP}", flush=True)
    subprocess.Popen(["open", str(APP)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def screen(name: str):
    sh("screencapture", "-x", str(OUT / name))


def app_pids() -> list[int]:
    out = subprocess.run(["pgrep", "-f", "Cherry Move.app/Contents/MacOS"], capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


def windows_of(pid: int) -> list[dict]:
    """The app's windows on screen (Quartz; needs no permissions)."""
    import Quartz
    infos = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
    return [dict(w) for w in infos if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer") == 0]


def system_events(pid: int, body: str, timeout=60) -> str:
    """Runs AppleScript against the app's process in System Events; the output, or 'ERROR: …'."""
    script = (f'tell application "System Events"\ntell (first process whose unix id is {pid})\n'
              f'set frontmost to true\n{body}\nend tell\nend tell')
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "ERROR: timed out"
    out = r.stdout.strip() if r.returncode == 0 else "ERROR: " + r.stderr.strip()
    print(f"[System Events] {body.splitlines()[0]} -> {out}", flush=True)
    return out


AX_FIND = '''
on labelOf(e)
  tell application "System Events"
    set out to ""
    repeat with a in {"AXTitle", "AXDescription", "AXValue"}
      try
        set v to value of attribute a of e
        if v is not missing value and v is not "" then set out to out & "|" & (v as text)
      end try
    end repeat
    return out & "|"
  end tell
end labelOf

on findIn(e, label, depth, seen)
  if depth > 30 then return missing value
  tell application "System Events"
    try
      set kids to UI elements of e
    on error
      return missing value
    end try
  end tell
  repeat with k in kids
    set l to my labelOf(k)
    try
      tell application "System Events" to set r to role of k
    on error
      set r to "?"
    end try
    set end of seen to r & l
    if l contains ("|" & label & "|") then
      try
        tell application "System Events"
          set pos to position of k
          set sz to size of k
          set how to "click"
          if r is "AXDisclosureTriangle" then
            set value of attribute "AXFocused" of k to true
            set how to "space"
          end if
        end tell
        return {(item 1 of pos) + (item 1 of sz) / 2, (item 2 of pos) + (item 2 of sz) / 2, how}
      end try
    end if
    set found to my findIn(k, label, depth + 1, seen)
    if found is not missing value then return found
  end repeat
  return missing value
end findIn
'''


def ax_click(pid: int, label: str) -> bool:
    """Clicks the middle of the thing in the window's page called label, like a person would (found by walking
    the accessibility tree, which the web view only fills in once something asks for it, so it tries a few
    times)."""
    script = AX_FIND + f'''
tell application "System Events"
  set p to first process whose unix id is {pid}
  set frontmost of p to true
  set w to window 1 of p
end tell
set seen to {{}}
set xy to my findIn(w, "{label}", 0, seen)
if xy is missing value then
  set AppleScript's text item delimiters to linefeed
  return "not found" & linefeed & (seen as text)
end if
tell application "System Events"
  if item 3 of xy is "space" then
    key code 49
  else
    tell p to click at {{item 1 of xy, item 2 of xy}}
  end if
end tell
return "clicked"'''
    out = ""
    for _ in range(3):
        try:
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=180)
            out = r.stdout.strip() if r.returncode == 0 else "ERROR: " + r.stderr.strip()
        except subprocess.TimeoutExpired:
            out = "ERROR: timed out"
        print(f"[click {label!r}] {out.splitlines()[0] if out else ''}", flush=True)
        if out == "clicked":
            return True
        time.sleep(2)
    (OUT / f"ax-tree-{label.split()[0].lower()}.txt").write_text(out)
    return False


def dismiss_gatekeeper():
    for proc_name in ("CoreServicesUIAgent", "UserNotificationCenter"):
        subprocess.run(["osascript", "-e", f'tell application "System Events" to if exists process "{proc_name}" '
                        f'then click (first button of window 1 of process "{proc_name}" whose name is "Done")'],
                       capture_output=True, timeout=30)
    sh("killall", "-9", "CoreServicesUIAgent")


# ---------------------------------------------------------------- 1. as downloaded
downloads = Path.home() / "Downloads"
downloads.mkdir(exist_ok=True)
dmg = downloads / DMG.name
shutil.copy(DMG, dmg)
sh("xattr", "-w", "com.apple.quarantine", f"0083;{int(time.time()):x};Safari;", str(dmg), check=True)
report["dmg_format"] = next((l for l in sh("hdiutil", "imageinfo", str(dmg)).splitlines() if "Format:" in l), "")
check("verified" in sh("hdiutil", "verify", str(dmg)).lower(), "the DMG's checksum is right")
sh("hdiutil", "attach", str(dmg), check=True)
check(VOLUME.is_dir(), "the DMG opens as a disk called Cherry Move")
report["dmg_contents"] = sorted(p.name for p in VOLUME.iterdir())
check((VOLUME / "Applications").is_symlink() and os.readlink(VOLUME / "Applications") == "/Applications",
      "it has the Applications shortcut to drag the app onto")
check((VOLUME / ".VolumeIcon.icns").exists(), "it has its own disk icon")
sh("open", str(VOLUME))
time.sleep(4)
screen("1-dmg-window.png")
shutil.rmtree(APP, ignore_errors=True)
sh("ditto", str(VOLUME / "Cherry Move.app"), str(APP), check=True)  # what dragging it to Applications does
report["quarantined_after_copy"] = "com.apple.quarantine" in sh("xattr", str(APP))
if not report["quarantined_after_copy"]:  # Finder marks what it copies off a downloaded disk image
    sh("xattr", "-w", "-r", "com.apple.quarantine", f"0083;{int(time.time()):x};Safari;", str(APP))
sh("osascript", "-e", 'tell application "Finder" to close every window')
sh("hdiutil", "detach", str(VOLUME))
report["arch"] = sh("lipo", "-archs", str(EXE))
check(set(report["arch"].split()) == {"x86_64", "arm64"}, "the app runs natively on Intel and Apple Silicon")
report["codesign"] = sh("codesign", "-dv", "--verbose=2", str(APP))
check("valid on disk" in sh("codesign", "--verify", "--deep", "--strict", "-vv", str(APP)),
      "its (ad-hoc) signature is intact")
report["gatekeeper"] = sh("spctl", "-a", "-vv", "-t", "exec", str(APP))
launch()
time.sleep(8)
screen("2-downloaded-first-open.png")
report["window_when_quarantined"] = any(windows_of(p) for p in app_pids())
sh("pkill", "-f", "Cherry Move.app/Contents/MacOS")
dismiss_gatekeeper()

# ---------------------------------------------------------------- 2. after "Open Anyway"
sh("xattr", "-dr", "com.apple.quarantine", str(APP))
running_file = Path.home() / "Library/Application Support/CherryMove/running.json"
launch()
pid = wait(lambda: (app_pids() or [None])[0], 30)
check(pid is not None and wait(running_file.exists, 30), "it opens after Open Anyway")
time.sleep(5)
screen("3-opened.png")
if pid:
    check(len(wait(lambda: windows_of(pid), 20) or []) == 1, "in its own window (not the browser)")
    system_events(pid, 'keystroke "q" using command down')
    if not wait(lambda: not app_pids(), 15):
        sh("pkill", "-f", "Cherry Move.app/Contents/MacOS")
time.sleep(2)

# ---------------------------------------------------------------- 3. the window, with test files
old_home = Path.home() / "OldMac"
shutil.rmtree(old_home, ignore_errors=True)
old_home.mkdir()
hashes = subprocess.run([sys.executable, str(HERE / "make_files.py"), str(old_home)], capture_output=True, text=True,
                        check=True).stdout
(OUT / "hashes.txt").write_text(hashes)
report["test_files"] = len(hashes.splitlines())
env = {**os.environ, "HOME": str(old_home)}
old_running = old_home / "Library/Application Support/CherryMove/running.json"
app_log = open(OUT / "window-app-output.txt", "a")


def start_app():
    return subprocess.Popen([str(EXE)], env=env, stdout=app_log, stderr=subprocess.STDOUT)


def call(path, body=None, timeout=120):
    info = json.loads(old_running.read_text())
    base, token = info["url"].split("/?t=")
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Cherry": token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


STICK = Path("/Volumes/USB3")


def send_to_stick(name: str):
    """Starts a move of the ticked things onto the stick, clicking through the window like a person (through
    the app's API if clicking isn't allowed)."""
    s = wait(lambda: (lambda s: s if s["catsReady"] else None)(call("/api/state")), 60)
    if report["clicks_allowed"]:
        clicked = ax_click(app.pid, "Get started")
        time.sleep(2)
        clicked = clicked and ax_click(app.pid, "Next")
        if clicked:
            wait(lambda: call("/api/state").get("sizes"), 120)
            time.sleep(4)  # the page looks for sticks every 2.5 s
            screen(name)
            clicked = ax_click(app.pid, "Next")
        if check(clicked and wait(lambda: call("/api/state")["sending"], 30), "a move can be clicked through"):
            return
    call("/api/plan", {"ids": [c["id"] for c in s["cats"] if c["default"]]})
    wait(lambda: call("/api/state").get("sizes"), 120)
    call("/api/send", {"storage": "usb", "drive": str(STICK), "level": 0})


sh("hdiutil", "create", "-size", "512m", "-fs", "MS-DOS FAT32", "-volname", "USB3", "-layout", "NONE",
   "-type", "UDIF", "-ov", "/tmp/stick3.dmg", check=True)
sh("hdiutil", "attach", "/tmp/stick3.dmg", check=True)
app = start_app()
try:
    check(wait(old_running.exists, 30), "the window opens")
    time.sleep(4)
    screen("4-window.png")
    check(call("/api/state").get("window") is True, "the page knows it's in the app's window")
    check(len(windows_of(app.pid)) == 1, "one window")
    probe = system_events(app.pid, "get name of window 1")
    report["clicks_allowed"] = not probe.startswith("ERROR")
    if not report["clicks_allowed"]:
        report["clicks_error"] = probe

    second = subprocess.run([str(EXE)], env=env, capture_output=True, timeout=20)
    time.sleep(1)
    check(second.returncode == 0 and app.poll() is None and len(windows_of(app.pid)) == 1,
          "opening it again brings this one to the front instead of opening a second")

    send_to_stick("5-where.png")
    wait(lambda: call("/api/state")["sending"], 30)
    if report["clicks_allowed"]:
        system_events(app.pid, 'click (first button of window 1 whose subrole is "AXCloseButton")')
    time.sleep(1.5)
    screen("5-close-while-sending.png")
    if report["clicks_allowed"]:
        check(app.poll() is None and len(windows_of(app.pid)) == 1, "closing while sending keeps the window open")
        if not ax_click(app.pid, "Stop and close"):
            if call("/api/state")["sending"]:
                check(False, "it asks, and Stop and close is there to click")
            else:
                report["stop_and_close"] = "the move finished before it could be stopped"
            call("/api/quit", {})
        else:
            check(True, "it asks, and Stop and close is there to click")
    else:
        call("/api/quit", {})
    check(wait(lambda: app.poll() is not None, 40) is not None, "Stop and close ends the app")
    check(not old_running.exists(), "running.json is gone afterwards")
    if app.poll() is None:
        app.kill()
        old_running.unlink(missing_ok=True)

    if not STICK.exists():
        sh("hdiutil", "attach", "/tmp/stick3.dmg", check=True)
    app = start_app()
    wait(old_running.exists, 30)
    check(json.loads(old_running.read_text())["pid"] == app.pid and app.poll() is None, "it opens again")
    time.sleep(3)
    send_to_stick("6-where.png")
    s = wait(lambda: (lambda s: s if not s["sending"] and (s.get("result") or s.get("sendErr")) else None)(
        call("/api/state")), 900)
    check(bool(s and s.get("result")), "the move finished: " + str(s and (s.get("sendErr") or s["result"]["files"])))
    code = s["code"] if s else ""
    time.sleep(3)
    screen("6-done.png")
    check(not STICK.exists(), "the stick was ejected when the move finished")

    sheets_before = len(windows_of(app.pid))
    if not (report.get("clicks_allowed") and ax_click(app.pid, "Show the code as a backup") and
            check(ax_click(app.pid, "Print code"), "Print code can be clicked")):
        call("/api/print", {})
    time.sleep(4)
    screen("7-print.png")
    check(len(windows_of(app.pid)) > sheets_before, "the print box opens over the window")
    system_events(app.pid, "key code 53")  # Escape: cancel printing
    check(wait(lambda: len(windows_of(app.pid)) == sheets_before, 10), "the print box can be cancelled")

    target = old_home / "Documents" / "Cherry Move code.txt"
    target.unlink(missing_ok=True)
    if not (report.get("clicks_allowed") and
            check(ax_click(app.pid, "Save code as a file…"), "Save code as a file… can be clicked")):
        threading.Thread(target=lambda: call("/api/save-code", {}, 300), daemon=True).start()
    time.sleep(4)
    screen("8-save-box.png")
    check(len(windows_of(app.pid)) > sheets_before, "the save box opens")
    system_events(app.pid, "key code 36")  # Return: save with the suggested name, in Documents
    check(wait(target.exists, 20) and code and code in target.read_text(), "the code was saved in Documents")
    screen("9-after-save.png")

    out = system_events(app.pid, 'keystroke "q" using command down')
    if out.startswith("ERROR"):
        call("/api/quit", {})
    check(wait(lambda: app.poll() is not None, 20) is not None, "Quit ends the app")
finally:
    if app.poll() is None:
        app.terminate()
    log = old_home / "Library/Application Support/CherryMove/log.txt"
    if log.exists():
        shutil.copy(log, OUT / "window-app-log.txt")
    if STICK.exists():
        sh("hdiutil", "detach", str(STICK), "-force")

# ---------------------------------------------------------------- 4. a whole move in Safari
for n in (1, 2):
    sh("hdiutil", "create", "-size", "128m", "-fs", "MS-DOS FAT32", "-volname", f"USB{n}", "-layout", "NONE",
       "-type", "UDIF", "-ov", str(OUT / f"stick{n}.dmg"), check=True)
sh("hdiutil", "attach", str(OUT / "stick1.dmg"), check=True)

app_out = OUT / "app-output.txt"
proc = subprocess.Popen([str(EXE), "--no-browser"], env=env, stdout=open(app_out, "w"), stderr=subprocess.STDOUT)
url = ""
for _ in range(120):
    url = next((w for w in app_out.read_text().split() if w.startswith("http://")), "")
    if url or proc.poll() is not None:
        break
    time.sleep(0.5)
if not url:
    raise SystemExit("the app didn't open:\n" + app_out.read_text())
token = url.split("t=")[1]
print("app is at", url, flush=True)


def state() -> dict:
    req = urllib.request.Request(url.split("/?")[0] + "/api/state", headers={"X-Cherry": token})
    return json.load(urllib.request.urlopen(req))


print("starting Safari", flush=True)
driver = webdriver.Safari()
driver.set_window_size(1000, 900)
driver.set_page_load_timeout(60)
# The page redraws the stick list every 2.5 s, so an element can go stale between finding and clicking it.
FLAKY = (StaleElementReferenceException, ElementNotInteractableException, ElementClickInterceptedException)
web_wait = WebDriverWait(driver, 120, poll_frequency=0.5, ignored_exceptions=FLAKY)
shots = iter(range(10, 100))


def shot(name: str):
    driver.save_screenshot(str(OUT / f"{next(shots)}-{name}.png"))


def click(css: str):
    def attempt(d):
        el = d.find_element(By.CSS_SELECTOR, css)
        if not el.is_enabled():
            return False
        el.click()
        return True
    web_wait.until(attempt)


def click_until(css: str, done, tries: int = 6):
    for _ in range(tries):
        click(css)
        try:
            WebDriverWait(driver, 15, poll_frequency=0.5, ignored_exceptions=FLAKY).until(done)
            return
        except TimeoutException:
            continue
    raise TimeoutException(f"clicking {css} didn't do anything")


def click_button(starts: str):
    def attempt(d):
        for b in d.find_elements(By.CSS_SELECTOR, "#dialog button"):
            if b.text.startswith(starts):
                b.click()
                return True
        return False
    web_wait.until(attempt)


def text_on_page(words: str) -> bool:
    return words in driver.find_element(By.ID, "app").text


try:
    driver.get(url)
    web_wait.until(lambda d: d.find_elements(By.ID, "go"))
    shot("home")
    click("#go")
    web_wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, "#cats input"))
    report["categories"] = driver.find_element(By.ID, "cats").text
    shot("choose")
    click("#next")
    web_wait.until(lambda d: d.find_element(By.ID, "next").text == "Next")
    web_wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, "#drive option[value='/Volumes/USB1']"))
    web_wait.until(lambda d: Select(d.find_element(By.ID, "drive")).select_by_value("/Volumes/USB1") or True)
    time.sleep(1)
    report["size_text"] = driver.find_element(By.ID, "desc").text
    report["fit_text"] = driver.find_element(By.ID, "usb").text
    shot("where")
    click_until("#next", lambda d: not d.find_element(By.ID, "dialog").get_attribute("hidden"))
    report["dialog"] = driver.find_element(By.ID, "dialog").text
    shot("too-small")
    click_button("Use ")
    web_wait.until(lambda d: text_on_page("is full") or text_on_page("All done"))
    shot("stick-full")
    if text_on_page("is full"):
        # The app ejects a full stick itself; unplug it if it's still there, then plug in stick 2.
        for _ in range(20):
            if not Path("/Volumes/USB1").exists():
                break
            time.sleep(0.5)
        report["ejected_full_stick"] = not Path("/Volumes/USB1").exists()
        if Path("/Volumes/USB1").exists():
            sh("hdiutil", "detach", "/Volumes/USB1", "-force")
        sh("hdiutil", "attach", str(OUT / "stick2.dmg"), check=True)
    web_wait.until(lambda d: text_on_page("All done"))
    report["done_text"] = driver.find_element(By.ID, "app").text
    s = state()
    report["result"] = s["result"]
    check(bool(s["result"]), "a whole move in Safari onto two sticks")
    shot("done")
    click("#finish")
    time.sleep(3)
    report["app_quit_after_finish"] = proc.poll() is not None
except Exception as e:  # noqa: BLE001
    report["error"] = repr(e)
    try:
        shot("error")
        report["page_text"] = driver.find_element(By.ID, "app").text
    except Exception:  # noqa: BLE001
        pass
finally:
    try:
        driver.quit()
    except Exception:  # noqa: BLE001
        pass
    if proc.poll() is None:
        proc.terminate()
    for v in ("/Volumes/USB1", "/Volumes/USB2"):
        if Path(v).exists():
            sh("hdiutil", "detach", v, "-force")
    log = old_home / "Library/Application Support/CherryMove/log.txt"
    if log.exists():
        shutil.copy(log, OUT / "app-log.txt")
    failed = [k for k, v in report["checks"].items() if not v]
    report["failed"] = failed
    (OUT / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))
sys.exit(1 if "error" in report or report["failed"] else 0)
