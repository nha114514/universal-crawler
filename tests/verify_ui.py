"""Browser interaction and render checks against the real local desktop API."""
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from crawler_system.config import settings
from crawler_system.service import ensure_service
from playwright.sync_api import sync_playwright


def main():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = settings()["browser_cache"]
    url, secret = ensure_service()
    directory = ROOT / "evidence"
    directory.mkdir(exist_ok=True)
    checks, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width":1440,"height":1000}, accept_downloads=True)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.goto(url + "/#token=" + secret)
        page.wait_for_load_state("networkidle")
        page.get_by_text("本机服务已连接", exact=True).wait_for()
        print("Rendered buttons:", page.get_by_role("button").all_text_contents()[:12], flush=True)
        page.screenshot(path=str(directory / "desktop-before.png"), full_page=True)
        page.get_by_label("网址或关键词").fill("https://example.com")
        page.get_by_role("button",name=re.compile("开始采集")).click()
        page.get_by_text("采集任务已创建",exact=True).wait_for()
        page.locator('#live-task').wait_for(state='hidden', timeout=60000)
        checks.append("submit_and_finish_real_web_job")
        page.get_by_label("搜索资料库").fill("Example Domain")
        page.wait_for_timeout(800)
        page.locator("#library-list .item-row").filter(has_text="Example Domain").first.click()
        page.locator("#inspector").wait_for(state="visible")
        page.get_by_role("button", name="正文", exact=True).click()
        assert "documentation examples" in page.locator("#detail-body").inner_text()
        checks.append("local_search_and_read")
        with page.expect_download() as received:
            page.get_by_role("button",name="导出这一条",exact=True).click()
        received.value.save_as(str(directory / "ui-export.md"))
        assert "example.com" in (directory / "ui-export.md").read_text(encoding="utf-8")
        checks.append("export_from_inspector")
        page.screenshot(path=str(directory / "desktop-reading.png"), full_page=True)
        page.get_by_role("button",name="关闭预览").click()
        page.locator('[data-view="jobs"]').click()
        page.get_by_role("heading",name="任务记录").wait_for()
        checks.append("task_history")
        page.locator('[data-view="tools"]').click()
        page.get_by_role("heading",name="工具与设置").wait_for()
        page.get_by_text("已安装",exact=True).first.wait_for()
        checks.append("tool_configuration_status")
        page.locator('[data-view="library"]').click()
        page.set_viewport_size({"width":600,"height":900})
        page.screenshot(path=str(directory / "desktop-narrow.png"), full_page=True)
        assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
        checks.append("narrow_window_no_horizontal_overflow")
        browser.close()
    (directory / "ui-verification.json").write_text(json.dumps({"checks":checks,"page_errors":errors},ensure_ascii=False,indent=2),encoding="utf-8")
    assert not errors, errors
    print("UI PASS",checks,flush=True)


if __name__ == "__main__":
    main()
