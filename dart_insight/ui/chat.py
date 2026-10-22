"""Pure session helpers: histories are keyed by receipt, never by company."""
def history_for(histories, receipt):
    return histories.setdefault(receipt, [])


def save_turn(histories, receipt, question, result):
    history_for(histories, receipt).extend([
        {'role': 'user', 'content': question},
        {'role': 'assistant', 'content': result['answer'], 'citations': result['citations'],
         'audit': result.get('audit', {})}])


def group_citations(citations):
    """One source link, retaining every actually cited chunk number and type."""
    groups = {}
    for citation in citations:
        source = citation['source']
        group = groups.setdefault(source, {'source': source, 'titles': [], 'citations': []})
        if citation['title'] not in group['titles']:
            group['titles'].append(citation['title'])
        if not any(c['number'] == citation['number'] for c in group['citations']):
            group['citations'].append(citation)
    return list(groups.values())


def separate_legacy_analysis(histories, analyses, analysis_question):
    """Migrate prior in-session automatic-analysis turns without discarding chat."""
    for receipt, turns in histories.items():
        kept, latest, index = [], None, 0
        while index < len(turns):
            turn = turns[index]
            if (turn.get('role') == 'user' and turn.get('content') == analysis_question and
                    index + 1 < len(turns) and turns[index + 1].get('role') == 'assistant'):
                latest = turns[index + 1]
                index += 2
            else:
                kept.append(turn)
                index += 1
        if latest is not None:
            analyses.setdefault(receipt, latest)
            histories[receipt] = kept


def render_copy_option(text):
    """Use Streamlit's supported code-block clipboard button, with no custom JS."""
    import streamlit as st
    with st.expander('전체 답변 복사'):
        st.caption('오른쪽 위 복사 아이콘을 누르면 아래 텍스트 전체가 클립보드에 복사됩니다.')
        st.code(text, language=None, wrap_lines=True)
