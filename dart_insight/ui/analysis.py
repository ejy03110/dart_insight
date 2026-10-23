"""Session-scoped automatic analysis. Never start a second implicit attempt."""
def ensure_analysis(receipt, analyses, attempts, run, *, retry=False):
    if receipt in analyses:
        return analyses[receipt]
    if attempts.get(receipt) in ('running', 'failed') and not retry:
        return None
    attempts[receipt] = 'running'
    try:
        result = run()
        if result is None:
            attempts[receipt] = 'failed'
            return None
        saved = {'content': result['answer'], 'citations': result['citations'],
                 'audit': result.get('audit', {})}
        analyses[receipt] = saved
        attempts[receipt] = 'complete'
        return saved
    except BaseException:
        # A disconnected/rerun-interrupted attempt must require deliberate retry.
        attempts[receipt] = 'failed'
        raise


def followup_history(receipt, analyses, histories):
    """Keep automatic analysis in provider context without duplicating visible chat."""
    from dart_insight.rag.prompts.answer import ANALYSIS_QUESTION
    previous = []
    if receipt in analyses:
        previous = [{'role': 'user', 'content': ANALYSIS_QUESTION, 'rcept_no': receipt},
                    {'role': 'assistant', 'content': analyses[receipt]['content'], 'rcept_no': receipt}]
    return previous + [dict(turn) for turn in histories.get(receipt, [])]
