import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from my_agent_app.database import Base


class ReportStatus(StrEnum):
    EM_ANALISE = "ANALYZING"
    COMPLETO = "COMPLETE"
    INCOMPLETO = "INCOMPLETE"
    CORRIGINDO = "FIXING"
    CORRIGIDO = "FIXED"
    FALHA_CORRECAO = "FIX_FAILED"


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ReportStatus.EM_ANALISE
    )
    event_uids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def title(self) -> str:
        """Extract problem title from the first '## Problem N: title' heading."""
        import re
        for line in self.markdown.splitlines():
            # Match English format: ## Problem 1: title
            m = re.match(r"^##\s+Problem(?:\s+\d+)?\s*:\s*(.+)", line.strip())
            if m:
                return m.group(1).strip()
            # Fallback: match legacy Portuguese format
            m = re.match(r"^##\s+Problema(?:\s+\d+)?\s*:\s*(.+)", line.strip())
            if m:
                return m.group(1).strip()
        return ""

    def severity(self) -> str:
        """Extract severity from first ## Problem section."""
        for line in self.markdown.splitlines():
            stripped = line.strip()
            if "**Severity:**" in stripped or "**Severidade:**" in stripped:
                levels = (
                    "CRITICAL", "HIGH", "MEDIUM", "LOW",
                    "CRITICO", "ALTO", "MEDIO", "BAIXO",
                )
                for level in levels:
                    if level in stripped.upper():
                        mapping = {
                            "CRITICO": "CRITICAL",
                            "ALTO": "HIGH",
                            "MEDIO": "MEDIUM",
                            "BAIXO": "LOW",
                        }
                        return mapping.get(level, level)
        return "—"
