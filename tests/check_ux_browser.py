"""Chromium regression over synthetic providers; never a live API validation."""
from pathlib import Path
import re
from playwright.sync_api import sync_playwright, expect


def check(page, label):
    expect(page.get_by_test_id('stException')).to_have_count(0)
    size=page.evaluate('({w:innerWidth,d:document.documentElement.scrollWidth,b:document.body.scrollWidth})')
    assert size['w']==size['d']==size['b'],(label,size)
    print(label,size,flush=True)


def menu(page,label):
    opener=page.get_by_test_id('stExpandSidebarButton')
    if opener.is_visible():opener.click()
    page.get_by_test_id('stRadioOption').filter(has=page.get_by_text(label,exact=True)).click()
    if page.viewport_size['width']<700:
        page.get_by_test_id('stSidebar').get_by_test_id('stBaseButton-headerNoPadding').click()


with sync_playwright() as playwright:
    browser=playwright.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
    for width,height in ((1440,1100),(390,844)):
        # Each size gets a new session, while fixture provider counts are global.
        page=browser.new_page(viewport={'width':width,'height':height})
        page.goto('http://127.0.0.1:8513')
        expect(page.get_by_text('오늘의 AI 브리핑',exact=True)).to_be_visible()
        expect(page.get_by_test_id('stMarkdownContainer').get_by_text(re.compile(r'^검증 전용 분석 \[근거 1\] · 호출 \d+$'))).to_be_visible()
        counter=page.get_by_test_id('stMarkdownContainer').get_by_text(re.compile(r'^검증 전용 분석 \[근거 1\] · 호출 \d+$')).last
        expect(counter).to_have_text(re.compile(r'^검증 전용 분석 \[근거 1\] · 호출 \d+$'))
        initial=int(counter.inner_text().split('호출 ')[-1])
        expect(page.get_by_role('button',name='브라우저 검증용 보유현황 3',exact=True)).to_be_visible()
        cards=page.locator('[class*="st-key-company_card_"]')
        expect(cards).to_have_count(3)
        page.evaluate('() => document.fonts.ready.then(() => true)')
        boxes=cards.evaluate_all('(elements) => elements.map(e => { const b=e.getBoundingClientRect(); return {x:b.x,y:b.y,width:b.width,height:b.height}; })')
        if width>1100:
            assert max(b['y'] for b in boxes)-min(b['y'] for b in boxes)<2,boxes
        else:
            assert boxes[0]['y']<boxes[1]['y']<boxes[2]['y'],boxes
        check(page,f'{width} automatic home/responsive cards')
        page.screenshot(path=f'/tmp/dart-ux-{width}-home.png')
        expect(page.get_by_text('최근 7일간 새로운 공시가 없습니다.',exact=True)).to_have_count(2)
        page.get_by_role('button',name='브라우저 검증용 보유현황 1',exact=True).click()
        expect(page.get_by_test_id('stMarkdownContainer').get_by_text(re.compile(r'^검증 전용 분석 \[근거 1\] · 호출 \d+$'))).to_be_visible()
        # Homepage representative is the newest receipt (3), including automatic preparation, so opening 1 adds exactly one.
        expect(counter).to_have_text(f'검증 전용 분석 [근거 1] · 호출 {initial+1}')
        detail_url=page.url
        heading=page.locator('.st-key-disclosure_heading')
        expect(heading).to_be_visible()
        box=heading.bounding_box();header=page.get_by_test_id('stHeader').bounding_box()
        assert box['y']>=header['y']+header['height'],(width,box,header)
        page.get_by_text('근거 내용 및 문서 식별 정보',exact=True).click()
        expect(page.get_by_text('인용 구간 1',exact=False)).to_be_visible()
        assert 'doc:internal-test-identifier' not in page.locator('body').inner_text()
        assert '010-0000-0000' not in page.locator('body').inner_text()
        page.get_by_text('공시 원문 전체 및 표 확인',exact=True).click()
        page.get_by_role('button',name='공시 본문 불러오기',exact=True).or_(page.get_by_role('button',name='본문 새로고침',exact=True)).click()
        expect(page.get_by_text('보유 주식 수 비교',exact=True)).to_be_visible()
        expect(page.get_by_test_id('stVegaLiteChart')).to_have_count(1)
        page.get_by_test_id('stVegaLiteChart').evaluate("element => element.scrollIntoView({block: 'center'})")
        expect(page.get_by_test_id('stVegaLiteChart')).to_be_visible()
        page.screenshot(path=f'/tmp/dart-ux-{width}-chart.png')
        expect(counter).to_have_text(f'검증 전용 분석 [근거 1] · 호출 {initial+1}')
        page.get_by_role('textbox',name='선택 공시에 대해 추가 질문하세요',exact=True).fill('후속 질문')
        page.get_by_role('textbox',name='선택 공시에 대해 추가 질문하세요',exact=True).press('Enter')
        expect(page.get_by_test_id('stChatMessage')).to_have_count(2)
        expect(counter).to_have_text(f'검증 전용 분석 [근거 1] · 호출 {initial+2}')
        check(page,f'{width} detail/chart/chat')
        page.screenshot(path=f'/tmp/dart-ux-{width}-detail.png',full_page=True)
        page.get_by_role('button',name='← 공시 목록으로 돌아가기',exact=True).click()
        expect(page.get_by_role('button',name='공시 검색',exact=True)).to_be_visible()
        assert page.get_by_text('공시 범위',exact=True).count()==0
        company=page.get_by_role('textbox',name='기업명·종목코드·기업코드 (선택)',exact=True)
        company.fill('00126380')
        dates=page.get_by_test_id('stDateInput').locator('input')
        dates.nth(0).fill('2020-01-01');dates.nth(0).press('Tab')
        dates.nth(1).fill('2020-12-31');dates.nth(1).press('Tab');page.keyboard.press('Escape')
        page.get_by_role('button',name='공시 검색',exact=True).click()
        expect(page.get_by_text('총 45건',exact=False)).to_be_visible()
        expect(page.get_by_role('button',name='공시 상세 보기',exact=True)).to_have_count(20)
        page.get_by_role('button',name='〉',exact=True).click()
        expect(page.get_by_text('현재 2페이지',exact=False)).to_be_visible()
        page.get_by_role('button',name='공시 상세 보기',exact=True).first.click()
        expect(page.get_by_test_id('stMarkdownContainer').get_by_text(re.compile(r'^검증 전용 분석 \[근거 1\] · 호출 \d+$'))).to_be_visible()
        # No answer is rendered on the unindexed detail; returning to records
        # is covered by AppTest's exact provider call counts.
        page.get_by_role('button',name='← 공시 목록으로 돌아가기',exact=True).click()
        expect(page.get_by_text('현재 2페이지',exact=False)).to_be_visible()
        check(page,f'{width} integrated server pagination/return')
        page.screenshot(path=f'/tmp/dart-ux-{width}-search.png',full_page=True)
        # Cold direct URL restores item. Conversation stays session-only by design.
        cold=browser.new_page(viewport={'width':width,'height':height});cold.goto(detail_url)
        expect(cold.locator('.st-key-disclosure_heading')).to_be_visible()
        expect(cold.get_by_test_id('stChatMessage')).to_have_count(0)
        box=cold.locator('.st-key-disclosure_heading').bounding_box();header=cold.get_by_test_id('stHeader').bounding_box()
        assert box['y']>=header['y']+header['height'],(width,box,header)
        cold.reload()
        expect(cold.locator('.st-key-disclosure_heading')).to_be_visible()
        box=cold.locator('.st-key-disclosure_heading').bounding_box()
        assert box['y']>=header['y']+header['height'],(width,box,header)
        check(cold,f'{width} cold detail URL/reload/header clearance')
        cold.close();page.close()
    tablet=browser.new_page(viewport={'width':900,'height':1100})
    tablet.goto('http://127.0.0.1:8513')
    cards=tablet.locator('[class*="st-key-company_card_"]')
    expect(cards).to_have_count(3)
    expect(tablet.get_by_text('현재 세션에 저장된 분석입니다.',exact=False)).to_be_visible()
    tablet.evaluate('() => document.fonts.ready.then(() => true)')
    boxes=cards.evaluate_all('(elements) => elements.map(e => { const b=e.getBoundingClientRect(); return {x:b.x,y:b.y,width:b.width,height:b.height}; })')
    assert abs(boxes[0]['y']-boxes[1]['y'])<2 and boxes[2]['y']>boxes[0]['y'],boxes
    check(tablet,'900 tablet two-column cards')
    tablet.screenshot(path='/tmp/dart-ux05-900-home.png')
    tablet.close()
    browser.close()
