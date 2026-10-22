"""Optional real-browser check; install Playwright separately from production deps.

Start tests/browser_clipboard_fixture.py via Streamlit from the repo root, then
run this script. This is not discovered by unittest; it never clears real caches.
"""
import argparse
import json

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--chromium', help='Use an existing Chromium binary')
    args = parser.parse_args()
    results = []
    with sync_playwright() as p:
        options = {'executable_path': args.chromium} if args.chromium else {}
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'], **options)
        context = browser.new_context(permissions=['clipboard-read', 'clipboard-write'])
        page = context.new_page()
        page.goto(args.url)
        title = page.get_by_text('분석 결과 복사 테스트', exact=True).first
        title.wait_for()
        marker = page.get_by_text('CACHE_MARKER:', exact=False).inner_text()
        for selected in (False, True):
            page.evaluate('window.getSelection().removeAllRanges()')
            if selected:
                title.evaluate('e=>{const r=document.createRange();r.selectNodeContents(e);const s=window.getSelection();s.addRange(r)}')
            page.keyboard.press('Control+c')
            assert page.get_by_test_id('stClearCacheDialog').count() == 0
            if selected:
                assert page.evaluate('navigator.clipboard.readText()') == '분석 결과 복사 테스트'
            results.append({'selected': selected, 'ctrl_c_dialog': False})
        page.evaluate('window.getSelection().removeAllRanges()')
        page.keyboard.press('c')
        page.wait_for_timeout(150)
        assert page.get_by_test_id('stClearCacheDialog').count() == 0
        for index, expected in enumerate(['분석 결과 복사 테스트\n- 테스트 전용 요약 [근거 1]',
                                           '채팅 답변 복사 테스트\n테스트 전용 답변 [근거 2]']):
            page.get_by_text('전체 답변 복사', exact=True).nth(index).click()
            block = page.get_by_test_id('stCode').nth(index)
            block.focus()
            page.keyboard.press("Shift")  # keyboard focus reveals Streamlit's built-in toolbar
            button = block.get_by_role('button')
            button.click()
            page.wait_for_function('(text)=>navigator.clipboard.readText().then(value=>value===text)', arg=expected)
            results.append({'copy_button': index, 'clipboard_exact_match': True})
        page.get_by_role('button', name='캐시 유지 확인', exact=True).click()
        page.wait_for_timeout(250)
        assert page.get_by_text('CACHE_MARKER:', exact=False).inner_text() == marker
        results.append({'resource_cache_preserved': True, 'plain_c_dialog': False})
        print(json.dumps({'browser': 'Chromium', 'checks': results}, ensure_ascii=False))
        browser.close()


if __name__ == '__main__':
    main()
