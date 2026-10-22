"""Optional real Chromium navigation regression; see docs/disclosure-search.md."""
import os
from urllib.parse import urlparse,parse_qs
from playwright.sync_api import sync_playwright,expect

def menu(page,label):
 opener=page.get_by_test_id('stExpandSidebarButton')
 if opener.is_visible():opener.click()
 page.get_by_test_id('stRadioOption').filter(has=page.get_by_text(label,exact=True)).click()
 if page.viewport_size['width']<700:
  page.get_by_test_id('stSidebar').get_by_test_id('stBaseButton-headerNoPadding').click()

def query(page):return parse_qs(urlparse(page.url).query)
def check(page,label):
 expect(page.get_by_test_id('stException')).to_have_count(0)
 sizes=page.evaluate('({w:innerWidth,d:document.documentElement.scrollWidth,b:document.body.scrollWidth})')
 assert sizes['w']==sizes['d']==sizes['b'],sizes
 print(label,sizes,'history',page.evaluate('history.length'),flush=True)

with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
 for width,height in [(1440,1100),(390,844)]:
  page=b.new_page(viewport={'width':width,'height':height})
  page.goto(os.environ.get('SEARCH_BROWSER_URL','http://127.0.0.1:8512/'))
  expect(page.locator('.research-hero')).to_have_count(1)
  initial=page.evaluate('history.length')
  menu(page,'공시 탐색')
  expect(page.get_by_role('button',name='공시 검색',exact=True)).to_be_visible()
  page.get_by_role('textbox',name='기업명·종목코드·기업코드 (선택)',exact=True).fill('00126380')
  dates=page.get_by_test_id('stDateInput').locator('input')
  dates.nth(0).fill('2020-01-01');dates.nth(0).press('Tab')
  dates.nth(1).fill('2020-12-31');dates.nth(1).press('Tab');page.keyboard.press('Escape')
  page.get_by_role('button',name='공시 검색',exact=True).click()
  expect(page.get_by_text('총 45건',exact=False)).to_be_visible()
  expect(page.get_by_role('button',name='공시 상세 보기',exact=True)).to_have_count(20)
  page.get_by_role('button',name='〉',exact=True).click()
  expect(page.get_by_text('현재 2페이지',exact=False)).to_be_visible()
  assert query(page)['p']==['2'] and query(page)['begin']==['2020-01-01'],page.url
  check(page,f'{width} page2')
  page.screenshot(path=f'/tmp/search-{width}-pagination.png')
  page.get_by_role('button',name='공시 상세 보기',exact=True).first.click()
  expect(page.get_by_role('button',name='공시 본문 불러오기',exact=True)).to_be_visible()
  detail_url=page.url
  assert query(page)['v']==['detail'] and query(page)['p']==['2']
  assert page.evaluate('history.length')>=initial+4
  print("BEFORE BACK",page.url,flush=True)
  page.go_back()
  print("AFTER BACK",page.url,flush=True)
  expect(page.get_by_text('현재 2페이지',exact=False)).to_be_visible()
  assert urlparse(page.url).path=='/explore'
  page.go_forward()
  expect(page.get_by_role('button',name='공시 본문 불러오기',exact=True)).to_be_visible()
  assert page.url==detail_url
  page.reload()
  expect(page.get_by_role('button',name='공시 본문 불러오기',exact=True)).to_be_visible()
  assert page.url==detail_url
  cold=b.new_page(viewport={'width':width,'height':height});cold.goto(detail_url)
  expect(cold.get_by_role('button',name='공시 본문 불러오기',exact=True)).to_be_visible()
  expect(cold.locator('.original-document')).to_have_count(0)
  check(cold,f'{width} direct detail URL');cold.close()
  page.get_by_role('button',name='← 공시 목록으로 돌아가기',exact=True).click()
  expect(page.get_by_text('현재 2페이지',exact=False)).to_be_visible()
  page.get_by_role('button',name='≫',exact=True).click()
  expect(page.get_by_role('button',name='공시 상세 보기',exact=True)).to_have_count(5)
  expect(page.get_by_role('button',name='〉',exact=True)).to_be_disabled()
  page.get_by_role('button',name='≪',exact=True).click()
  expect(page.get_by_text('현재 1페이지',exact=False)).to_be_visible()
  check(page,f'{width} back/forward/reload/first/last passed')
  page.close()
 b.close()
