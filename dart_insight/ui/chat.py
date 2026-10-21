"""Pure session helpers: histories are keyed by receipt, never by company."""
def history_for(histories, receipt):
    return histories.setdefault(receipt, [])


def save_turn(histories, receipt, question, result):
    history_for(histories, receipt).extend([
        {'role': 'user', 'content': question},
        {'role': 'assistant', 'content': result['answer'], 'citations': result['citations'],
         'audit': result.get('audit', {})}])
