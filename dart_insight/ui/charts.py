"""Responsive original-source charts; no model or source requests."""
from dart_insight.services.quantitative import holdings_comparisons


def render_original_chart(view, disclosure, *, citation_numbers=()):
    import streamlit as st
    comparisons=holdings_comparisons(view.html) if view.state=='success' and view.html else []
    if not comparisons:
        st.caption('단위와 비교 항목이 명확한 원문 표가 없어 수치 차트를 표시하지 않습니다.')
        return
    import pandas as pd
    for comparison in comparisons:
        st.subheader('보유 주식 수 비교')
        st.caption(f'{disclosure.corp_name} · {disclosure.report_nm} · 공시일 {disclosure.rcept_dt} · 단위: {comparison.unit}. 비교 기준은 원문의 직전보고서 / 이번보고서입니다.')
        st.bar_chart(pd.DataFrame(comparison.values, columns=['보고 시점', comparison.label]).set_index('보고 시점'), x_label='보고 시점', y_label='보유 수량 (주)', sort=False, width='stretch')
        st.caption('근거: 원문 표의 명시 값' + (' · 인용 ' + ', '.join(f'[근거 {n}]' for n in citation_numbers) if citation_numbers else ' · AI 답변과 독립적으로 원문 표에서 확인한 비교'))
        st.link_button('차트 출처 원문 ↗', disclosure.source_url)
