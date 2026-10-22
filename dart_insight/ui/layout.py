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
    .block-container {max-width:1320px;padding-top:2rem;padding-bottom:3rem;}
    h1,h2,h3 {color:#102c50;letter-spacing:-.035em;}
    [data-testid="stVerticalBlockBorderWrapper"] {border-radius:16px;}
    .st-key-disclosure_analysis, .st-key-disclosure_chat, .company-card {
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
    .company-card {height:100%;min-height:130px;margin-bottom:.75rem;}
    .company-card h3 {margin:0;font-size:1.1rem;}
    .company-card p {margin:.3rem 0;color:#526982;}
    .company-card strong {font-size:1.3rem;color:#163d66;}
    [data-testid="stLinkButton"] a {border-radius:10px;}
    @media(max-width:700px) {
        .block-container {padding:1rem .8rem 2rem;}
        .research-hero {padding:1.4rem;min-height:180px;}
        [data-testid="stHorizontalBlock"]:has(.company-card) {flex-wrap:wrap;}
        [data-testid="stColumn"]:has(.company-card) {min-width:100%;}
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


def render_company_overview(disclosures, companies, search):
    # Counts describe stored metadata, never a claim about the latest market data.
    from html import escape
    visible = [c for c in companies if not search or search.casefold() in c.name.casefold() or search in c.stock_code]
    st.subheader('기업 한눈에 보기')
    st.caption('현재 저장된 공시 기준 · 기업명 또는 종목코드로 아래 공시 목록을 좁힐 수 있습니다.')
    if not visible:
        return
    for column, company in zip(st.columns(len(visible)), visible):
        count = sum(d.corp_name == company.name for d in disclosures)
        with column:
            st.markdown(f'''<div class="company-card"><h3>{escape(company.name)}</h3>
                <p>{escape(company.stock_code)}</p><strong>{count:,}건</strong><p>분석 가능한 저장 공시</p></div>''',
                unsafe_allow_html=True)


def render_disclosure_header(selected):
    st.divider()
    st.caption(f'{selected.corp_name} · {selected.rcept_dt} · {selected.disclosure_type}')
    st.subheader(selected.report_nm)
    st.link_button('공식 DART 원문 보기 ↗', selected.source_url)
    with st.expander('공시 식별 정보'):
        st.text(f'접수번호: {selected.rcept_no}')
