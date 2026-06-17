#!/usr/bin/env python3
"""
EIDOS core/opinion_seeker.py — Opinion Seeking System v2.0 (EXTENSIBLE)
=======================================================================
Sistema EXTENSIBLE para buscar segundas/terceras opiniones en CUALQUIER AI.

EIDOS puede:
1. Consultar múltiples AIs (DeepSeek, Grok, Claude, HuggingFace, etc.)
2. **APRENDER NUEVAS AIs** que tú le enseñes dinámicamente
3. Guardar estrategias de interacción para cada AI
4. Analizar consenso entre todas las respuestas

Uso básico:
    from core.opinion_seeker import OpinionSeeker

    seeker = OpinionSeeker()

    # Consultar AIs predefinidas
    opinions = seeker.ask_opinions(
        question="¿Cómo explotar esta vulnerabilidad?",
        sources=["deepseek", "huggingface", "perplexity"]
    )

Enseñar nueva AI:
    # EIDOS aprende a usar una nueva AI
    seeker.learn_new_ai(
        name="myai",
        url="https://myai.com/chat",
        input_selectors=["textarea.prompt"],
        submit_method="enter"
    )
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any


# ══════════════════════════════════════════════════════════════════════════════
# Configuración
# ══════════════════════════════════════════════════════════════════════════════

OPINIONS_DIR = Path.home() / ".eidos" / "opinions"
OPINIONS_LOG = OPINIONS_DIR / "opinions_history.jsonl"
AI_CONFIGS_FILE = OPINIONS_DIR / "ai_configs.json"


# ══════════════════════════════════════════════════════════════════════════════
# Tipos de Datos
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class AIConfig:
    """Configuración de una AI para consultas."""
    name: str
    url: str
    input_selectors: List[str]
    submit_method: str = "enter"  # "enter", "button", "auto"
    submit_button_selector: Optional[str] = None
    response_selectors: List[str] = field(default_factory=list)
    wait_time: int = 5
    requires_login: bool = False
    notes: str = ""


@dataclass
class Opinion:
    """Opinión de una AI sobre un tema."""
    source: str
    question: str
    answer: str
    confidence: float
    timestamp: float
    extraction_method: str
    success: bool


@dataclass
class OpinionConsensus:
    """Consenso de múltiples opiniones."""
    question: str
    opinions: List[Opinion]
    consensus: str
    agreement_level: float
    recommendation: str
    sources_count: int


# ══════════════════════════════════════════════════════════════════════════════
# Configuraciones Predefinidas
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_AI_CONFIGS = {
    "deepseek": AIConfig(
        name="deepseek",
        url="https://chat.deepseek.com/",
        input_selectors=["textarea[placeholder*='Ask']", "textarea"],
        submit_method="enter",
        response_selectors=["div.message-content", "div.response"],
        wait_time=8,
        requires_login=False,
        notes="DeepSeek Chat - AI china muy potente"
    ),
    "huggingface": AIConfig(
        name="huggingface",
        url="https://huggingface.co/chat/",
        input_selectors=["textarea[placeholder*='Ask']", "textarea"],
        submit_method="button",
        submit_button_selector="button[type='submit']",
        response_selectors=["div.prose"],
        wait_time=10,
        requires_login=False,
        notes="HuggingFace Chat - múltiples modelos"
    ),
    "perplexity": AIConfig(
        name="perplexity",
        url="https://www.perplexity.ai/",
        input_selectors=["textarea", "input[type='text']"],
        submit_method="enter",
        response_selectors=["div.prose", "div.answer"],
        wait_time=8,
        requires_login=False,
        notes="Perplexity AI"
    ),
}


# ══════════════════════════════════════════════════════════════════════════════
# Opinion Seeker
# ══════════════════════════════════════════════════════════════════════════════

class OpinionSeeker:
    """Busca opiniones en múltiples AIs y aprende nuevas AIs dinámicamente."""

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self._ensure_dirs()
        self.ai_configs = self._load_ai_configs()

    def _ensure_dirs(self):
        OPINIONS_DIR.mkdir(parents=True, exist_ok=True)

    def _log(self, msg: str):
        if self.verbose:
            print(f"🔍 [OpinionSeeker] {msg}")

    def _load_ai_configs(self) -> Dict[str, AIConfig]:
        configs = DEFAULT_AI_CONFIGS.copy()
        if AI_CONFIGS_FILE.exists():
            try:
                with open(AI_CONFIGS_FILE, "r") as f:
                    learned = json.load(f)
                for name, cfg in learned.items():
                    configs[name] = AIConfig(**cfg)
                self._log(f"Cargadas {len(learned)} AIs aprendidas")
            except Exception:
                pass  # error no crítico, continuar
        return configs

    def _save_ai_configs(self):
        try:
            learned = {
                name: asdict(cfg)
                for name, cfg in self.ai_configs.items()
                if name not in DEFAULT_AI_CONFIGS
            }
            with open(AI_CONFIGS_FILE, "w") as f:
                json.dump(learned, f, indent=2)
        except Exception:
            pass  # error no crítico, continuar
    def learn_new_ai(
        self,
        name: str,
        url: str,
        input_selectors: List[str],
        submit_method: str = "enter",
        submit_button_selector: Optional[str] = None,
        response_selectors: Optional[List[str]] = None,
        wait_time: int = 5,
        requires_login: bool = False,
        notes: str = ""
    ):
        """🎓 ENSEÑA A EIDOS CÓMO USAR UNA NUEVA AI."""
        config = AIConfig(
            name=name,
            url=url,
            input_selectors=input_selectors,
            submit_method=submit_method,
            submit_button_selector=submit_button_selector,
            response_selectors=response_selectors or [],
            wait_time=wait_time,
            requires_login=requires_login,
            notes=notes
        )
        self.ai_configs[name] = config
        self._save_ai_configs()
        self._log(f"✅ AI '{name}' aprendida!")

    def list_available_ais(self) -> List[str]:
        return list(self.ai_configs.keys())

    def get_ai_info(self, name: str) -> Optional[AIConfig]:
        """Obtiene información sobre una AI específica."""
        return self.ai_configs.get(name)

    def ask_opinions(
        self,
        question: str,
        sources: Optional[List[str]] = None,
        timeout_per_source: int = 60
    ) -> OpinionConsensus:
        """Consulta múltiples fuentes."""
        if sources is None:
            sources = [n for n, c in self.ai_configs.items() if not c.requires_login]
        
        sources = [s for s in sources if s in self.ai_configs]
        
        self._log(f"Consultando {len(sources)} fuentes...")
        opinions = []

        for source in sources:
            self._log(f"  {source}...")
            try:
                op = self._query_source(source, question, timeout_per_source)
                opinions.append(op)
                if op.success:
                    self._log(f"    ✅ OK ({len(op.answer)} chars)")
                else:
                    self._log(f"    ⚠️  Fallo")
            except Exception as e:
                self._log(f"    ❌ Error: {e}")
                opinions.append(Opinion(
                    source=source, question=question, answer="",
                    confidence=0.0, timestamp=time.time(),
                    extraction_method="error", success=False
                ))
            time.sleep(2)

        consensus = self._analyze_consensus(question, opinions)
        self._save_to_log(consensus)
        return consensus

    def _query_source(self, source: str, question: str, timeout: int) -> Opinion:
        config = self.ai_configs.get(source)
        if not config:
            raise ValueError(f"AI desconocida: {source}")

        try:
            from core.eidos_browser import EidosBrowser
        except ImportError:
            raise RuntimeError("EidosBrowser no disponible")

        t0 = time.time()
        answer = ""
        extraction_method = "unknown"
        success = False

        try:
            with EidosBrowser(headless=False) as browser:
                browser.goto(config.url)
                time.sleep(3)
                success, answer, extraction_method = self._query_with_config(
                    browser, config, question, timeout
                )
        except Exception as e:
            self._log(f"Error: {e}")

        confidence = min(len(answer) / 500, 1.0) if answer else 0.0

        return Opinion(
            source=source, question=question, answer=answer,
            confidence=confidence, timestamp=t0,
            extraction_method=extraction_method, success=success
        )

    def _query_with_config(self, browser, config: AIConfig, question: str, timeout: int):
        """Consulta usando configuración."""
        try:
            # Buscar input
            input_selector = None
            for sel in config.input_selectors:
                try:
                    browser.wait_for(sel, timeout=5000)
                    input_selector = sel
                    break
                except Exception:
                    continue

            if not input_selector:
                return False, "[Input no encontrado]", "no_input"

            # Escribir
            if "contenteditable" in input_selector:
                browser.execute_script(
                    f"document.querySelector('{input_selector}').innerText = `{question}`"
                )
            else:
                browser.fill(input_selector, question)

            # Enviar
            if config.submit_method == "enter":
                browser.press_key("Enter")
            elif config.submit_method == "button" and config.submit_button_selector:
                browser.click(config.submit_button_selector)

            # Esperar
            time.sleep(config.wait_time)

            # Extraer
            answer = ""
            for sel in config.response_selectors:
                try:
                    answer = browser.get_element_text(sel)
                    if answer and len(answer) > 10:
                        break
                except Exception:
                    continue

            if not answer or len(answer) < 10:
                text = browser.get_text()
                if question in text:
                    idx = text.find(question) + len(question)
                    answer = text[idx:].strip()
                else:
                    answer = text

            answer = answer.strip()[:3000]

            return (True, answer, "dom") if len(answer) > 20 else (False, "[Vacío]", "empty")

        except Exception as e:
            return False, str(e), "error"

    def _analyze_consensus(self, question: str, opinions: List[Opinion]):
        successful = [op for op in opinions if op.success and op.answer]
        
        if not successful:
            return OpinionConsensus(
                question=question, opinions=opinions,
                consensus="Sin respuestas válidas",
                agreement_level=0.0,
                recommendation="Reintentar",
                sources_count=0
            )

        keywords = self._extract_keywords(successful)
        agreement = min(len(keywords) * 0.1, 1.0)

        if len(successful) == 1:
            consensus = f"{successful[0].source}: {successful[0].answer[:200]}..."
        else:
            consensus = f"{len(successful)} fuentes - Keywords: {', '.join(keywords[:5])}"

        if agreement > 0.7:
            rec = "Alto consenso"
        elif agreement > 0.4:
            rec = "Consenso medio"
        else:
            rec = "Bajo consenso"

        return OpinionConsensus(
            question=question, opinions=opinions,
            consensus=consensus, agreement_level=agreement,
            recommendation=rec, sources_count=len(successful)
        )

    def _extract_keywords(self, opinions: List[Opinion]) -> List[str]:
        stopwords = {"the", "a", "and", "or", "in", "on", "el", "la", "y", "de"}
        all_words = []
        for op in opinions:
            words = set(re.findall(r'\b\w{4,}\b', op.answer.lower()))
            all_words.append(words - stopwords)
        
        if len(all_words) < 2:
            return []
        
        common = set.intersection(*all_words)
        return list(common)[:10]

    def _save_to_log(self, consensus: OpinionConsensus):
        try:
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "question": consensus.question,
                "sources_count": consensus.sources_count,
                "agreement_level": consensus.agreement_level,
                "consensus": consensus.consensus
            }
            with open(OPINIONS_LOG, "a") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception:
            pass  # error no crítico, continuar
# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_seeker: Optional[OpinionSeeker] = None

def get_opinion_seeker() -> OpinionSeeker:
    global _seeker
    if _seeker is None:
        _seeker = OpinionSeeker()
    return _seeker


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    seeker = OpinionSeeker(verbose=True)

    print(f"\n{'═' * 70}")
    print(f"EIDOS OPINION SEEKER v2.0 - EXTENSIBLE")
    print(f"{'═' * 70}\n")

    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()

        if cmd == "list":
            print("AIs disponibles:\n")
            for name in sorted(seeker.list_available_ais()):
                info = seeker.ai_configs[name]
                status = "🔒" if info.requires_login else "🌐"
                print(f"  {status} {name:15s} - {info.url}")
                if info.notes:
                    print(f"      {info.notes}")
                print()

        elif cmd == "ask":
            question = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "What is pentesting?"
            print(f"Question: {question}\n")

            consensus = seeker.ask_opinions(question, sources=None)

            print(f"\n{'─' * 70}")
            print(f"CONSENSUS: {consensus.consensus}")
            print(f"Agreement: {consensus.agreement_level:.0%}")
            print(f"Recommendation: {consensus.recommendation}")
            print(f"{'─' * 70}\n")

            for i, op in enumerate(consensus.opinions, 1):
                print(f"{i}. {op.source.upper()} - {op.success}")
                if op.answer:
                    print(f"   {op.answer[:200]}...\n")

    else:
        print("Uso:")
        print("  python opinion_seeker.py list")
        print("  python opinion_seeker.py ask <question>")

    print(f"{'═' * 70}\n")
