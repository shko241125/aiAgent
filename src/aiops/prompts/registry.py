"""Prompt 관리 (4.3).

프롬프트를 코드에서 분리해 `templates/*.md` 로 관리한다 (리뷰·버전관리·A/B 테스트 용이).
파일 형식:
    ---
    version: 1
    description: ...
    ---
    ===system===
    시스템 프롬프트 ($변수 치환)
    ===user===
    사용자 프롬프트

치환은 string.Template(`$name`, `${name}`) 을 쓴다 — JSON 예시의 `{}` 와 충돌하지 않도록.
TODO(4.3): 프롬프트 버전별 평가(eval) 데이터셋과 회귀 테스트, DB 기반 동적 관리.
"""

import re
from pathlib import Path
from string import Template

from pydantic import BaseModel

TEMPLATE_DIR = Path(__file__).parent / "templates"
_FRONT = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


class PromptTemplate(BaseModel):
    name: str
    version: str = "1"
    description: str = ""
    system: str
    user: str = "$input"


class RenderedPrompt(BaseModel):
    name: str
    version: str
    system: str
    user: str


def parse_template(name: str, raw: str) -> PromptTemplate:
    meta: dict[str, str] = {}
    m = _FRONT.match(raw)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        raw = raw[m.end() :]
    parts = re.split(r"^===(system|user)===\s*$", raw, flags=re.MULTILINE)
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    return PromptTemplate(
        name=name,
        version=meta.get("version", "1"),
        description=meta.get("description", ""),
        system=sections.get("system", raw.strip()),
        user=sections.get("user", "$input"),
    )


class PromptRegistry:
    def __init__(self, directory: Path = TEMPLATE_DIR) -> None:
        self._templates: dict[str, PromptTemplate] = {}
        for p in sorted(directory.glob("*.md")):
            self._templates[p.stem] = parse_template(p.stem, p.read_text(encoding="utf-8"))

    def register(self, template: PromptTemplate) -> None:
        self._templates[template.name] = template

    def get(self, name: str) -> PromptTemplate:
        return self._templates[name]

    def names(self) -> list[str]:
        return sorted(self._templates)

    def render(self, name: str, **variables: object) -> RenderedPrompt:
        t = self.get(name)
        vars_ = {k: str(v) for k, v in variables.items()}
        return RenderedPrompt(
            name=t.name,
            version=t.version,
            system=Template(t.system).safe_substitute(vars_),
            user=Template(t.user).safe_substitute(vars_),
        )
