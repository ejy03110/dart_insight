"""Display-only privacy reduction; original citation payloads remain unchanged."""
import re
from urllib.parse import urlparse, parse_qs


def public_excerpt(text):
    # Mask labelled contact/identity fields only. Never alter quantitative evidence.
    lines = text.splitlines()
    output = []
    suppress_next = False
    labels = re.compile(r'^\s*(?:생년월일|주민등록번호|연락처|전화번호|휴대전화|주소|소재지)\s*[:：]?\s*(.*)$')
    for line in lines:
        if suppress_next:
            output.append('[개인정보 표시 생략]')
            suppress_next = False
            continue
        match = labels.match(line)
        if match:
            output.append(line[:match.start(1)] + '[개인정보 표시 생략]')
            suppress_next = not bool(match[1])
        else:
            # Phone/email repetition is unnecessary in an evidence excerpt.
            line = re.sub(r'(?<!\d)(?:0\d{1,2})[- ]\d{3,4}[- ]\d{4}(?!\d)', '[연락처 표시 생략]', line)
            line = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', '[이메일 표시 생략]', line)
            output.append(line)
    return '\n'.join(output)


def source_receipt(source):
    parsed = urlparse(source)
    if parsed.scheme != 'https' or parsed.hostname != 'dart.fss.or.kr':
        return None
    value = parse_qs(parsed.query).get('rcpNo', [''])[0]
    return value if re.fullmatch(r'\d{14}', value) else None


def filing_date(source):
    value = source_receipt(source)
    if not value:
        return '접수일 미확인'
    from datetime import date
    try:
        return '접수일 ' + date(int(value[:4]), int(value[4:6]), int(value[6:8])).isoformat()
    except ValueError:
        return '접수일 미확인'
