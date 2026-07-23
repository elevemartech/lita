"""
agent/tools/analyzers/faq_analyzer.py — Análise determinística de FAQs.

Zero LLM. Zero chamadas de API. Recebe list[FaqItem] e devolve FaqAnalysisResult.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime

from schemas.faq_schemas import FaqAnalysisResult, FaqIssue, FaqItem


class FaqAnalyzer:
    """Análise determinística de FAQs — sem LLM, sem APIs externas."""

    # Categorias reais de FAQ.CATEGORY_CHOICES na eleve-api (apps/faqs/models.py) —
    # EN, não PT-BR. Ver também _VALID_CATEGORIES em agent/tools/faq_tools.py.
    VALID_CATEGORIES = [
        "Admission",
        "Pricing",
        "Uniform",
        "Schedule",
        "Documentation",
        "Activities",
        "Meals",
        "Transport",
        "Pedagogical",
        "General",
    ]

    # Chaves em EN (batem com VALID_CATEGORIES / dados reais), keywords em
    # PT-BR (as perguntas das FAQs são em português).
    CATEGORY_KEYWORDS: dict[str, list[str]] = {
        "Pricing": [
            "mensalidade", "boleto", "pagamento", "valor", "taxa",
            "anuidade", "desconto", "multa",
        ],
        "Admission": [
            "matrícula", "rematrícula", "documentos", "transferência", "vaga", "enroll",
        ],
        "Schedule": [
            "horário", "portão", "recreio",
            "entrada", "saída", "turno",
        ],
        "Uniform": ["uniforme", "farda"],
        "Meals": [
            "cantina", "lanche", "almoço", "cardápio", "comida", "alimentação",
        ],
        "Transport": ["transporte", "van", "ônibus", "condução"],
    }

    STALE_DAYS = 90
    DUPLICATE_THRESHOLD = 0.75
    MIN_ANSWER_LENGTH = 20

    def analyze(self, faqs: list[FaqItem]) -> FaqAnalysisResult:
        """Entry point principal — executa todas as análises e agrega os resultados."""
        empty_issues = self.check_empty_answers(faqs)
        duplicate_issues, duplicate_groups = self.check_duplicates(faqs)
        category_issues = self.check_wrong_category(faqs)
        stale_issues = self.check_stale(faqs)
        quality_issues = self.check_quality(faqs)
        coverage_gaps = self.check_coverage_gaps(faqs)

        # FAQs com empty_answer não recebem também issue de quality
        empty_ids = {i.faq_id for i in empty_issues}
        quality_issues = [i for i in quality_issues if i.faq_id not in empty_ids]

        all_issues = (
            empty_issues + duplicate_issues + category_issues + stale_issues + quality_issues
        )

        return FaqAnalysisResult(
            total_faqs=len(faqs),
            issues=all_issues,
            coverage_gaps=coverage_gaps,
            duplicate_groups=duplicate_groups,
            stale_count=len(stale_issues),
            empty_count=len(empty_issues),
            quality_count=len(quality_issues),
        )

    def check_empty_answers(self, faqs: list[FaqItem]) -> list[FaqIssue]:
        """FAQs com answer vazio ou ausente."""
        issues = []
        for faq in faqs:
            if not faq.answer or not faq.answer.strip():
                issues.append(FaqIssue(
                    faq_id=faq.id,
                    question=faq.question,
                    issue_type="empty_answer",
                    description="Resposta vazia ou ausente.",
                    severity="error",
                ))
        return issues

    @staticmethod
    def _bigrams(text: str) -> set[tuple[str, str]]:
        """Cria conjunto de bigrams a partir do texto normalizado."""
        words = re.sub(r"[^\w\s]", "", text.lower()).split()
        if len(words) < 2:
            return set()
        return {(words[i], words[i + 1]) for i in range(len(words) - 1)}

    @staticmethod
    def _jaccard(a: set, b: set) -> float:
        """Similaridade de Jaccard entre dois conjuntos."""
        if not a and not b:
            return 1.0
        union = a | b
        return len(a & b) / len(union) if union else 0.0

    def check_duplicates(
        self, faqs: list[FaqItem]
    ) -> tuple[list[FaqIssue], list[list[int]]]:
        """
        Detecta perguntas similares via n-gram (bigrams) + Jaccard.
        Retorna issues E grupos de IDs duplicados.
        """
        ids = [faq.id for faq in faqs]
        bigrams_by_id = {faq.id: self._bigrams(faq.question) for faq in faqs}
        id_to_faq = {faq.id: faq for faq in faqs}

        # Detecta pares similares
        edges: list[tuple[int, int]] = []
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                if self._jaccard(bigrams_by_id[a], bigrams_by_id[b]) >= self.DUPLICATE_THRESHOLD:
                    edges.append((a, b))

        # Union-find para componentes conectados
        parent = {fid: fid for fid in ids}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int) -> None:
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        for a, b in edges:
            union(a, b)

        groups_map: dict[int, list[int]] = defaultdict(list)
        for fid in ids:
            groups_map[find(fid)].append(fid)

        duplicate_groups = [sorted(g) for g in groups_map.values() if len(g) > 1]

        issues = []
        for group in duplicate_groups:
            for fid in group:
                others = [i for i in group if i != fid]
                issues.append(FaqIssue(
                    faq_id=fid,
                    question=id_to_faq[fid].question,
                    issue_type="duplicate",
                    description=f"Pergunta similar à(s) FAQ(s) {others}.",
                    severity="warning",
                ))

        return issues, duplicate_groups

    def check_wrong_category(self, faqs: list[FaqItem]) -> list[FaqIssue]:
        """
        Detecta FAQs com categoria incorreta via keyword matching.
        Só emite issue quando 2+ keywords de outra categoria estão presentes.
        """
        issues = []
        for faq in faqs:
            question_lower = faq.question.lower()
            keyword_matches: dict[str, int] = {}
            for cat, keywords in self.CATEGORY_KEYWORDS.items():
                if cat == faq.category:
                    continue
                count = sum(1 for kw in keywords if kw in question_lower)
                if count >= 2:
                    keyword_matches[cat] = count
            if keyword_matches:
                best = max(keyword_matches, key=lambda k: keyword_matches[k])
                issues.append(FaqIssue(
                    faq_id=faq.id,
                    question=faq.question,
                    issue_type="wrong_category",
                    description=(
                        f"Parece ser da categoria '{best}' mas está em '{faq.category}'."
                    ),
                    severity="warning",
                ))
        return issues

    def check_stale(self, faqs: list[FaqItem]) -> list[FaqIssue]:
        """FAQs com updated_at > STALE_DAYS dias atrás."""
        issues = []
        now = datetime.now(UTC)
        for faq in faqs:
            if faq.updated_at is None:
                continue
            updated = faq.updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            days_old = (now - updated).days
            if days_old > self.STALE_DAYS:
                issues.append(FaqIssue(
                    faq_id=faq.id,
                    question=faq.question,
                    issue_type="stale",
                    description=f"Não actualizada há {days_old} dias.",
                    severity="info",
                ))
        return issues

    def check_quality(self, faqs: list[FaqItem]) -> list[FaqIssue]:
        """Respostas muito curtas (menos de MIN_ANSWER_LENGTH chars)."""
        issues = []
        for faq in faqs:
            stripped = (faq.answer or "").strip()
            if 0 < len(stripped) < self.MIN_ANSWER_LENGTH:
                issues.append(FaqIssue(
                    faq_id=faq.id,
                    question=faq.question,
                    issue_type="quality",
                    description=f"Resposta muito curta ({len(stripped)} chars).",
                    severity="warning",
                ))
        return issues

    def check_coverage_gaps(self, faqs: list[FaqItem]) -> list[str]:
        """Categorias de VALID_CATEGORIES sem nenhuma FAQ activa."""
        active_categories = {faq.category for faq in faqs if faq.status == "active"}
        return [cat for cat in self.VALID_CATEGORIES if cat not in active_categories]
