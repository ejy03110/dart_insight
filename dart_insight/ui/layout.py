"""Presentation-only layout: no API, model, database, or session mutation."""
from base64 import b64encode
from pathlib import Path

import streamlit as st


def apply_theme():
    st.markdown('''<style>
    :root {color-scheme: light;}
    [data-testid="stAppViewContainer"] {background:#f3f6fa;color:#142d4e;}
    [data-testid="stHeader"] {background:rgba(243,246,250,.94);}
    [data-testid="stSidebar"] {background:#152b40;}
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
    [data-testid="stSidebar"] label, [data-testid="stSidebar"] [data-testid="stCaptionContainer"] {color:#e6eef8;}
    [data-testid="stSidebar"] [data-testid="stExpander"] {background:#1e3953;}
    [data-testid="stSidebar"] button {border-color:#637d99;background:#254b70;color:#fff;}
    [data-testid="stSidebar"] button:hover {background:#32628e;color:#fff;}
    [data-testid="stSidebar"] [role="radiogroup"] label {padding:.45rem .6rem;border-radius:8px;}
    [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {background:#254b70;}
    .block-container {max-width:1320px;padding-top:5rem;padding-bottom:3rem;}
    h1,h2,h3 {color:#102c50;letter-spacing:-.035em;}
    [data-testid="stVerticalBlockBorderWrapper"] {border-radius:16px;}
    .st-key-disclosure_analysis, .st-key-disclosure_chat, [class*="st-key-company_card_"] {
        background:white;border:1px solid #dce4ee;border-radius:16px;padding:1.2rem;}
    .research-hero {position:relative;overflow:hidden;border-radius:20px;margin-bottom:1.4rem;
        background:linear-gradient(110deg,#fff,#e4edf5);min-height:210px;padding:2.2rem;}
    .research-hero img {position:absolute;inset:0;width:100%;height:100%;object-fit:cover;
        filter:blur(3px);opacity:.38;pointer-events:none;}
    .research-hero::after {content:'';position:absolute;inset:0;
        background:linear-gradient(90deg,rgba(255,255,255,.96),rgba(255,255,255,.15));}
    .research-hero .hero-copy {position:relative;z-index:1;max-width:720px;}
    .research-hero h1 {font-size:clamp(1.8rem,3.3vw,3rem);line-height:1.3;margin:.5rem 0;}
    .eyebrow {color:#326596;font-size:.9rem;font-weight:600;}
    [class*="st-key-company_card_"] {height:100%;min-height:130px;margin-bottom:.75rem;}
    .company-card h3 {margin:0;font-size:1.1rem;}
    .company-card p {margin:.3rem 0;color:#526982;}
    .company-card strong {font-size:1.3rem;color:#163d66;}
    [data-testid="stLinkButton"] a {border-radius:10px;}
    .st-key-detail_return {padding-top:12px;}
    .st-key-search_pagination button[kind="primary"] {background:#173e67;border-color:#173e67;color:white;opacity:1;}
    .original-document {max-width:100%;overflow-wrap:anywhere;line-height:1.8;}
    .original-document table {width:100%;table-layout:fixed;border-collapse:collapse;margin:1rem 0;}
    .original-document td,.original-document th {border:1px solid #dce4ee;padding:.5rem;white-space:pre-wrap;overflow-wrap:anywhere;}
    .original-document p,.document-text {white-space:pre-wrap;overflow-wrap:anywhere;}
    [data-testid="stMain"] {scroll-padding-top:5rem;}
    .st-key-company_grid [data-testid="stHorizontalBlock"] {flex-wrap:wrap;gap:.75rem;align-items:stretch;}
    .st-key-company_grid [data-testid="stColumn"] {flex:1 1 calc(33.333% - .75rem);min-width:0;}
    .st-key-company_grid [class*="st-key-company_card_"] {min-height:0;padding:.75rem;margin:0;gap:.35rem !important;}
    .st-key-company_grid h3 {font-size:1.15rem;}
    .st-key-company_grid button p {font-size:.86rem;line-height:1.4;overflow-wrap:anywhere;}
    .st-key-company_grid [data-testid="stCaptionContainer"] p {font-size:.76rem;margin-bottom:.35rem;}
    @media(min-width:701px) and (max-width:1100px) {
        .st-key-company_grid [data-testid="stColumn"] {flex:0 0 calc(50% - .75rem) !important;width:calc(50% - .75rem) !important;}
    }
    @media(max-width:700px) {
        .block-container {padding:4.5rem .8rem 2rem;}
        .research-hero {padding:1.4rem;min-height:180px;}
        [data-testid="stHorizontalBlock"]:has([class*="st-key-company_card_"]) {flex-wrap:wrap;}
        [data-testid="stColumn"]:has([class*="st-key-company_card_"]) {min-width:100%;}
        .st-key-company_grid [data-testid="stColumn"] {flex:0 0 100% !important;width:100% !important;}
        .st-key-disclosure_analysis,.st-key-disclosure_chat {padding:.8rem;}
    }
    </style>''', unsafe_allow_html=True)


def render_hero(section):
    asset = Path(__file__).with_name('assets') / 'buildings.webp'
    image = ''
    if asset.is_file():
        encoded = b64encode(asset.read_bytes()).decode('ascii')
        image = f'<img src="data:image/webp;base64,{encoded}" alt="" />'
    titles = {'홈': '공시를 읽는 새로운 방법.', '공시 탐색': '기업의 공시, 핵심부터 살펴보세요.',
              '분석 기록': '이전 분석에서 대화를 이어가세요.'}
    st.markdown(f'''<section class="research-hero">{image}<div class="hero-copy">
        <div class="eyebrow">DART Insight · 공시 기반 AI 리서치</div>
        <h1>{titles[section]}</h1>
        <p>공시 원문을 근거로 핵심 내용과 위험 요인을 확인하세요.</p>
        </div></section>''', unsafe_allow_html=True)


def render_company_overview(disclosures, companies, search, navigate, recent=None, open_recent=None):
    visible = [c for c in companies if not search or search.casefold() in c.name.casefold() or search in c.stock_code]
    st.subheader('기업 살펴보기')
    if not visible:
        st.info('검색한 기업을 찾을 수 없습니다.')
        return
    for column, company in zip(st.columns(len(visible)), visible):
        with column:
            with st.container(border=True, key='company_card_' + company.stock_code):
                st.subheader(company.name)
                st.caption(company.stock_code)
                if recent is not None:
                    from dart_insight.services.recent_disclosures import latest_three
                    from dart_insight.ui.recent import result_notice
                    result = recent[company.stock_code]
                    st.caption('최근 7일 공시')
                    result_notice(result)
                    for disclosure in latest_three(result):
                        st.button(disclosure.report_nm, key='recent_home_' + company.stock_code + '_' + disclosure.rcept_no,
                                  on_click=open_recent, args=(disclosure,))
                        st.caption(disclosure.rcept_dt)
                st.button('공시 살펴보기', key='company_' + company.stock_code,
                          on_click=navigate, args=('공시 탐색', None, company.name))



def render_disclosure_header(selected,*,show_source=True,show_identifiers=True):
    with st.container(key='disclosure_heading'):
        st.caption(f'{selected.corp_name} · {selected.rcept_dt} · {selected.disclosure_type}')
        st.subheader(selected.report_nm)
        if show_source:
            st.link_button('공식 DART 원문 보기 ↗', selected.source_url)
        if show_identifiers:
            with st.expander('공시 식별 정보'):
                st.text(f'접수번호: {selected.rcept_no}')


def reset_detail_scroll(receipt):
    """Reset once on detail entry, not while reading/chatting during reruns."""
    reset = st.session_state.get('_detail_scroll_receipt') != receipt
    st.session_state['_detail_scroll_receipt'] = receipt
    script = '''<script>
    requestAnimationFrame(() => requestAnimationFrame(() => {
        const main = document.querySelector('[data-testid="stMain"]');
        if (main) main.scrollTo({top: 0, left: 0, behavior: 'auto'});
    }));
    </script>'''
    # Keep the element position stable so unrelated expanders keep their state.
    st.html(script if reset else '<span hidden></span>', unsafe_allow_javascript=True)
