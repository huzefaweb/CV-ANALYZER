"""Pure assembly of parse-quality provenance from already-pure `parse_gates`
building blocks (Story 4.2, AC#1). Never invents skills/employment/etc.
content beyond what `classify_blocks` actually found — an absent content
class is simply absent from `blocks`, not synthesized.
"""

from __future__ import annotations

from dataclasses import dataclass

from .analysis_provider import ResumeSourceUnit
from .parse_gates import Block, GateCode, classify_blocks, evaluate_readable_content_gate, is_coherent_block


@dataclass(frozen=True)
class QualityProvenance:
    blocks: list[Block]
    gate_codes: list[GateCode]
    coherent_block_count: int


def build_quality_provenance(units: list[ResumeSourceUnit]) -> QualityProvenance:
    blocks = classify_blocks(units)
    normalized_text = "\n".join(u.text for u in units)
    gate_codes = evaluate_readable_content_gate(normalized_text, blocks)
    coherent_block_count = sum(1 for b in blocks if is_coherent_block(b))
    return QualityProvenance(blocks=blocks, gate_codes=gate_codes, coherent_block_count=coherent_block_count)


__all__ = ["QualityProvenance", "build_quality_provenance"]
