"""운영 지식 자동 축적 (M1-09 / 2.6).

해결된 인시던트를 포스트모템 문서로 만들어 RAG 에 넣고 DB 에 영속화한다.
→ 다음 장애 때 RCA·Knowledge 에이전트가 '우리 조직의 과거 사례'를 근거로 쓸 수 있다.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiops.db.models import KnowledgeDocRow
from aiops.domain.models import Incident
from aiops.rag.models import Document


def postmortem_document(incident: Incident, resolution: str) -> Document:
    actions = "\n".join(f"- {a}" for a in incident.actions) or "- (기록 없음)"
    when = f"{incident.created_at:%Y-%m-%d %H:%M}"
    text = f"""# [인시던트 기록] {incident.title}

## 요약
서비스: {incident.service} · 심각도: {incident.severity} · 발생: {when}

{incident.summary or "(요약 없음)"}

## 근본 원인
{incident.root_cause or "(미확정)"}

## 조치
{actions}

## 해결
{resolution}
"""
    return Document(
        id=f"incident-{incident.id}",
        text=text,
        metadata={
            "doc_type": "postmortem",
            "service": incident.service,
            "severity": str(incident.severity),
            "title": incident.title,
            "source": f"incident:{incident.id}",
        },
    )


class KnowledgeRepository:
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self.sessionmaker = sessionmaker

    async def upsert(self, doc: Document) -> None:
        async with self.sessionmaker() as s:
            row = await s.get(KnowledgeDocRow, doc.id)
            if row is None:
                s.add(KnowledgeDocRow(id=doc.id, text=doc.text, meta=doc.metadata))
            else:
                row.text, row.meta = doc.text, doc.metadata
            await s.commit()

    async def all(self) -> list[Document]:
        async with self.sessionmaker() as s:
            rows = await s.scalars(select(KnowledgeDocRow))
            return [Document(id=r.id, text=r.text, metadata=r.meta) for r in rows]
