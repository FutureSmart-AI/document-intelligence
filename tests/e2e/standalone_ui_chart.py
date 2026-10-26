"""Screenshot an answered chart question in the standalone UI (clean-install proof).

Signs in with STANDALONE_PASSWORD from the generated .env, opens Conversations for
the collection the API run created, asks the aggregate question and waits for the
answer's chart to render. Saves full-page screenshots to --out; prints no secret.
Requires `pip install playwright` + `playwright install chromium`.
"""
import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ui", default="http://localhost:8086")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True, help="summary.json from standalone_single_key.py")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    env = dict(line.split("=", 1) for line in args.env_file.read_text(encoding="utf-8").splitlines()
               if "=" in line and not line.startswith("#"))
    target = json.loads(args.summary.read_text(encoding="utf-8"))["ui_target"]
    args.out.mkdir(parents=True, exist_ok=True)
    base = args.ui.rstrip("/") + "/document-intelligence/"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.set_default_timeout(60_000)
        page.goto(base, wait_until="networkidle")
        # The form is a React-controlled input: a value typed before hydration never
        # reaches its state, and the POST goes out with an empty password. Type key by
        # key and retry until the login request itself answers 200.
        status = None
        for attempt in range(1, 4):
            field = page.get_by_label("Workspace password")
            field.fill("")
            field.press_sequentially(env["STANDALONE_PASSWORD"], delay=5)
            with page.expect_response(lambda r: r.url.endswith("/auth/login")) as login:
                page.get_by_role("button", name="Sign in").click()
            status = login.value.status
            print(f"login attempt {attempt}: HTTP {status}", flush=True)
            if status == 200:
                break
            page.wait_for_timeout(2_000)
        if status != 200:
            page.screenshot(path=str(args.out / "ui-0-login-failed.png"), full_page=True)
            raise SystemExit("UI sign-in failed")
        # A successful sign-in reloads the page itself; let that navigation finish
        # before ours, or ours is interrupted by it.
        page.wait_for_timeout(3_000)
        page.wait_for_load_state("networkidle")
        conversations = f"{base}conversations/?collection={target['collection']}"
        for attempt in range(1, 4):
            try:
                page.goto(conversations, wait_until="networkidle")
                break
            except Exception as exc:
                print(f"open conversations attempt {attempt}: {exc}", flush=True)
                page.wait_for_timeout(2_000)
        if page.get_by_label("Workspace password").count():
            page.screenshot(path=str(args.out / "ui-0-still-signed-out.png"), full_page=True)
            raise SystemExit("still on the sign-in page after a 200 login")
        page.screenshot(path=str(args.out / "ui-1-conversations.png"), full_page=True)
        box = page.get_by_role("textbox").last
        box.wait_for(state="visible")
        box.press_sequentially(target["question"], delay=5)
        box.press("Enter")
        print("question sent", flush=True)
        try:
            page.locator("svg.recharts-surface").first.wait_for(state="visible", timeout=240_000)
            page.wait_for_timeout(2_000)  # let the bars animate in
            ok = True
        except Exception as exc:  # screenshot whatever is there, then fail
            print(f"chart did not render: {exc}", file=sys.stderr)
            ok = False
        page.screenshot(path=str(args.out / "ui-2-answer-with-chart.png"), full_page=False)
        browser.close()
    print(json.dumps({"chart_rendered": ok, "screenshot": str(args.out / "ui-2-answer-with-chart.png")}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
