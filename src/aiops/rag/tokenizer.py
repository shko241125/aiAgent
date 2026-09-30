"""BM25 용 토크나이저.

한국어는 교착어라 공백 단위 토큰화로는 '장애가'/'장애를' 이 서로 다른 단어가 된다.
형태소 분석기(Kiwi, Mecab) 없이도 동작하도록 한글 구간은 문자 bigram 을 함께 생성한다.
TODO(4.2): kiwipiepy 형태소 분석기를 선택 의존성으로 붙여 비교 평가.
"""

import re

_TOKEN = re.compile(r"[가-힣]+|[a-zA-Z0-9_.\-]+")
_HANGUL = re.compile(r"^[가-힣]+$")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for tok in _TOKEN.findall(text.lower()):
        if _HANGUL.match(tok):
            if len(tok) == 1:
                tokens.append(tok)
            else:
                tokens.extend(tok[i : i + 2] for i in range(len(tok) - 1))
        else:
            tokens.append(tok)
            if "-" in tok or "." in tok or "_" in tok:  # order-service → order, service
                tokens.extend(t for t in re.split(r"[-._]", tok) if t)
    return tokens
