"""Extract comparable holdings only from labelled, unmerged original table cells."""
from dataclasses import dataclass
from html.parser import HTMLParser
import re


@dataclass(frozen=True)
class Comparison:
    label: str
    values: tuple[tuple[str, int], ...]
    unit: str = '주'


class Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables=[]; self.rows=None; self.row=None; self.cell=None; self.invalid=False; self.depth=0

    def handle_starttag(self, tag, attrs):
        if tag=='table':
            self.depth+=1
            if self.depth==1:self.rows=[];self.invalid=False
            else:self.invalid=True
        elif self.depth==1:
            if tag=='tr': self.row=[]
            elif tag in ('td','th'):
                if any(k in ('rowspan','colspan') and v!='1' for k,v in attrs):self.invalid=True
                self.cell=[]
            elif tag=='br' and self.cell is not None:self.cell.append(' ')

    def handle_data(self, data):
        if self.cell is not None:self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td','th') and self.depth==1 and self.cell is not None:
            if self.row is not None:self.row.append(''.join(self.cell).strip())
            self.cell=None
        elif tag=='tr' and self.depth==1 and self.row is not None:
            self.rows.append(self.row);self.row=None
        elif tag=='table':
            if self.depth==1 and not self.invalid:self.tables.append(self.rows)
            self.depth=max(0,self.depth-1)


def holdings_comparisons(html):
    """No flattening inference, merged headers, ratio/count mixing or arithmetic."""
    parser=Tables();parser.feed(html)
    results=[]
    normalize=lambda s:re.sub(r'\s+', '', s)
    quantity=re.compile(r'^(?:보유주식수|보유수량|주식수|주권수|특정증권등의수)\(주\)$')
    number=re.compile(r'^(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)$')
    for table in parser.tables:
        if len(table)<2 or len({len(row) for row in table})!=1:continue
        header=[normalize(x) for x in table[0]]
        if header == ['구분', '직전보고서', '이번보고서']:
            for row in table[1:]:
                label = normalize(row[0])
                values = [normalize(value) for value in row[1:]]
                if quantity.fullmatch(label) and all(number.fullmatch(value) for value in values):
                    results.append(Comparison(row[0], tuple(zip(header[1:], (int(value.replace(',', '')) for value in values)))))
            continue
        # Supported shape: 구분 | 보유주식수(주); 직전보고서 ...; 이번보고서 ...
        if len(header)!=2 or header[0] not in ('구분','보고구분') or not quantity.fullmatch(header[1]):continue
        values=[]
        for row in table[1:]:
            label=normalize(row[0]);value=normalize(row[1])
            if label in ('직전보고서','이번보고서') and number.fullmatch(value):
                values.append((label,int(value.replace(',',''))))
        if len(values)==2 and {label for label,_ in values}=={'직전보고서','이번보고서'}:
            ordered=tuple(sorted(values,key=lambda row:row[0]!='직전보고서'))
            results.append(Comparison(table[0][1], ordered))
    return results
