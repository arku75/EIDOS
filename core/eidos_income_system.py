"""
EIDOS Income System - Generador de Ingresos Autónomo
====================================================

Sistema de generación de ingresos para autosuficiencia económica de EIDOS.
Integra: Bug Bounty, Freelancing, Automatización de servicios, y Trading.

Filosofía:
- EIDOS debe financiar su propia operación (APIs, servidores, mejoras)
- Múltiples fuentes de ingreso para diversificación
- Automatización completa: detectar oportunidades, ejecutar, cobrar
- Transparencia total en ingresos y gastos

Fuentes de ingreso:
1. Bug Bounty: HackerOne, Bugcrowd, Intigriti (automatizado)
2. Freelancing: Upwork, Fiverr, Toptal (automatizado)
3. Servicios automatizados: Scraping, análisis, generación de contenido
4. Trading P2P entre agentes (ya existe)
5. Crypto: Trading, staking, yield farming (próximamente)

Uso:
    from core.eidos_income_system import IncomeSystem, get_income_system
    income = get_income_system()
    income.start_autonomous_mode()
"""

import json
import logging
import os
import sqlite3
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from core.db import get_conn

log = logging.getLogger("eidos.income_system")

DB_PATH = os.path.expanduser("~/.eidos/income_system.db")
CONFIG_PATH = os.path.expanduser("~/.eidos/income_config.json")
WALLET_PATH = os.path.expanduser("~/.eidos/wallet.json")

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    "bug_bounty": {
        "enabled": True,
        "platforms": ["hackerone", "bugcrowd", "intigriti"],
        "scan_interval_hours": 6,
        "min_bounty_usd": 100,
        "auto_submit": True,  # LIBERTY: Envía sin aprobación humana
        "scopes": ["web", "api", "mobile", "cloud"],
    },
    "freelancing": {
        "enabled": True,
        "platforms": ["upwork", "fiverr"],
        "scan_interval_hours": 4,
        "min_project_usd": 50,
        "max_project_usd": 5000,
        "auto_apply": True,  # LIBERTY: Aplica sin aprobación humana
        "skills": [
            "python", "automation", "scraping", "data_analysis",
            "web_development", "api_development", "scripting",
            "bug_hunting", "security_audit", "penetration_testing"
        ],
    },
    "automation_services": {
        "enabled": True,
        "services": {
            "web_scraping": {"price_usd": 25, "description": "Extracción de datos web"},
            "data_analysis": {"price_usd": 50, "description": "Análisis de datos con reportes"},
            "automation_script": {"price_usd": 75, "description": "Script de automatización personalizado"},
            "api_integration": {"price_usd": 100, "description": "Integración de APIs"},
            "security_audit": {"price_usd": 200, "description": "Auditoría de seguridad básica"},
            "code_review": {"price_usd": 30, "description": "Revisión de código Python"},
        },
        "advertise_interval_hours": 12,
    },
    "crypto_trading": {
        "enabled": False,  # Próximamente
        "paper_trading": True,  # Modo simulación hasta validar estrategia
        "exchanges": ["binance", "kraken"],
        "max_position_usd": 100,
        "daily_budget_usd": 50,
    },
    "expenses": {
        "api_costs_monthly": {
            "ollama": 0,  # Local, gratis
            "openrouter": 0,  # Variable
            "anthropic": 0,  # Variable
            "openai": 0,  # Variable
        },
        "server_costs_monthly": 0,
        "development_budget_monthly": 100,
    },
    "goals": {
        "monthly_income_target_usd": 500,
        "financial_buffer_months": 3,
        "reinvestment_rate": 0.3,  # 30% de ganancias se reinvierten en mejoras
    }
}

# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

class OpportunityType(str, Enum):
    BUG_BOUNTY = "bug_bounty"
    FREELANCE_JOB = "freelance_job"
    AUTOMATION_REQUEST = "automation_request"
    CRYPTO_TRADE = "crypto_trade"


class OpportunityStatus(str, Enum):
    DETECTED = "detected"
    ANALYZING = "analyzing"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class IncomeOpportunity:
    id: str
    type: OpportunityType
    title: str
    description: str
    source_platform: str
    potential_earnings_usd: float
    effort_hours_estimate: float
    required_skills: List[str]
    deadline: Optional[datetime]
    status: OpportunityStatus = OpportunityStatus.DETECTED
    created_at: datetime = field(default_factory=datetime.now)
    url: Optional[str] = None
    raw_data: Dict = field(default_factory=dict)
    
    @property
    def hourly_rate(self) -> float:
        if self.effort_hours_estimate == 0:
            return 0
        return self.potential_earnings_usd / self.effort_hours_estimate
    
    @property
    def is_expired(self) -> bool:
        if self.deadline is None:
            return False
        return datetime.now() > self.deadline


@dataclass  
class IncomeRecord:
    id: str
    opportunity_id: Optional[str]
    source: str
    amount_usd: float
    description: str
    timestamp: datetime = field(default_factory=datetime.now)
    platform_fees_usd: float = 0.0
    net_amount_usd: float = 0.0
    reinvested_usd: float = 0.0
    saved_usd: float = 0.0


@dataclass
class ExpenseRecord:
    id: str
    category: str
    amount_usd: float
    description: str
    timestamp: datetime = field(default_factory=datetime.now)
    is_recurring: bool = False
    recurrence_period: Optional[str] = None  # monthly, yearly


# ══════════════════════════════════════════════════════════════════════════════
#  BASE DE DATOS
# ══════════════════════════════════════════════════════════════════════════════

class IncomeDatabase:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        # Tabla de oportunidades
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS opportunities (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                source_platform TEXT,
                potential_earnings_usd REAL,
                effort_hours_estimate REAL,
                required_skills TEXT,  -- JSON list
                deadline TEXT,  -- ISO format
                status TEXT DEFAULT 'detected',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                url TEXT,
                raw_data TEXT  -- JSON
            )
        """)
        
        # Tabla de ingresos
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS income_records (
                id TEXT PRIMARY KEY,
                opportunity_id TEXT,
                source TEXT,
                amount_usd REAL,
                description TEXT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                platform_fees_usd REAL DEFAULT 0,
                net_amount_usd REAL DEFAULT 0,
                reinvested_usd REAL DEFAULT 0,
                saved_usd REAL DEFAULT 0
            )
        """)
        
        # Tabla de gastos
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS expense_records (
                id TEXT PRIMARY KEY,
                category TEXT,
                amount_usd REAL,
                description TEXT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                is_recurring INTEGER DEFAULT 0,
                recurrence_period TEXT
            )
        """)
        
        # Tabla de balance mensual
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS monthly_balance (
                year_month TEXT PRIMARY KEY,
                total_income REAL DEFAULT 0,
                total_expenses REAL DEFAULT 0,
                net_savings REAL DEFAULT 0,
                opportunities_count INTEGER DEFAULT 0,
                completed_count INTEGER DEFAULT 0
            )
        """)
        
        conn.commit()
        conn.close()
        log.debug("Income database initialized")
    
    def save_opportunity(self, opp: IncomeOpportunity):
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO opportunities 
            (id, type, title, description, source_platform, potential_earnings_usd,
             effort_hours_estimate, required_skills, deadline, status, created_at, url, raw_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            opp.id, opp.type.value, opp.title, opp.description, opp.source_platform,
            opp.potential_earnings_usd, opp.effort_hours_estimate,
            json.dumps(opp.required_skills),
            opp.deadline.isoformat() if opp.deadline else None,
            opp.status.value, opp.created_at.isoformat(), opp.url,
            json.dumps(opp.raw_data)
        ))
        conn.commit()
        conn.close()
    
    def get_opportunities(self, status: Optional[OpportunityStatus] = None, 
                         limit: int = 100) -> List[IncomeOpportunity]:
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        if status:
            cursor.execute(
                "SELECT * FROM opportunities WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status.value, limit)
            )
        else:
            cursor.execute(
                "SELECT * FROM opportunities ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
        
        rows = cursor.fetchall()
        conn.close()
        
        opportunities = []
        for row in rows:
            opportunities.append(IncomeOpportunity(
                id=row[0],
                type=OpportunityType(row[1]),
                title=row[2],
                description=row[3],
                source_platform=row[4],
                potential_earnings_usd=row[5],
                effort_hours_estimate=row[6],
                required_skills=json.loads(row[7]) if row[7] else [],
                deadline=datetime.fromisoformat(row[8]) if row[8] else None,
                status=OpportunityStatus(row[9]),
                created_at=datetime.fromisoformat(row[10]),
                url=row[11],
                raw_data=json.loads(row[12]) if row[12] else {}
            ))
        return opportunities
    
    def record_income(self, record: IncomeRecord):
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO income_records 
            (id, opportunity_id, source, amount_usd, description, timestamp,
             platform_fees_usd, net_amount_usd, reinvested_usd, saved_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.id, record.opportunity_id, record.source, record.amount_usd,
            record.description, record.timestamp.isoformat(),
            record.platform_fees_usd, record.net_amount_usd,
            record.reinvested_usd, record.saved_usd
        ))
        conn.commit()
        conn.close()
    
    def get_income_stats(self, days: int = 30) -> Dict[str, Any]:
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        since = (datetime.now() - timedelta(days=days)).isoformat()
        
        cursor.execute(
            "SELECT SUM(amount_usd), SUM(net_amount_usd), COUNT(*) FROM income_records WHERE timestamp > ?",
            (since,)
        )
        total, net, count = cursor.fetchone()
        conn.close()
        
        return {
            "total_income_usd": total or 0,
            "net_income_usd": net or 0,
            "transactions_count": count or 0,
            "period_days": days
        }


# ══════════════════════════════════════════════════════════════════════════════
#  SISTEMA PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class IncomeSystem:
    """
    Sistema de generación de ingresos autónomo para EIDOS.
    """
    
    def __init__(self):
        self.db = IncomeDatabase()
        self.config = self._load_config()
        self.running = False
        self.scan_thread: Optional[threading.Thread] = None
        self.opportunities_queue: List[IncomeOpportunity] = []
        self.wallet_balance_usd = 0.0
        
        log.info("Income System initialized")
    
    def _load_config(self) -> Dict:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, 'r') as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        return DEFAULT_CONFIG.copy()
    
    def save_config(self):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, 'w') as f:
            json.dump(self.config, f, indent=2)
    
    # ═════════════════════════════════════════════════════════════════════════
    #  BUG BOUNTY - Detección de oportunidades
    # ═════════════════════════════════════════════════════════════════════════
    
    def scan_bug_bounty_platforms(self) -> List[IncomeOpportunity]:
        """
        Escanea plataformas de bug bounty buscando programas activos.
        Nota: Esto requiere APIs/autenticación real para funcionar completamente.
        """
        opportunities = []
        
        if not self.config["bug_bounty"]["enabled"]:
            return opportunities
        
        min_bounty = self.config["bug_bounty"]["min_bounty_usd"]
        
        # Simulación de plataformas - En producción usar APIs reales
        platforms_data = {
            "hackerone": self._mock_hackerone_scan(),
            "bugcrowd": self._mock_bugcrowd_scan(),
            "intigriti": self._mock_intigriti_scan(),
        }
        
        for platform, programs in platforms_data.items():
            for prog in programs:
                if prog["min_bounty"] >= min_bounty:
                    opp = IncomeOpportunity(
                        id=f"bb_{platform}_{int(time.time())}_{len(opportunities)}",
                        type=OpportunityType.BUG_BOUNTY,
                        title=f"Bug Bounty: {prog['name']}",
                        description=prog["description"],
                        source_platform=platform,
                        potential_earnings_usd=prog["avg_bounty"],
                        effort_hours_estimate=prog["effort_hours"],
                        required_skills=prog["skills"],
                        deadline=datetime.now() + timedelta(days=prog["days_remaining"]),
                        url=prog["url"],
                        raw_data=prog
                    )
                    opportunities.append(opp)
                    self.db.save_opportunity(opp)
        
        log.info(f"Bug Bounty scan: {len(opportunities)} oportunidades encontradas")
        return opportunities
    
    def _mock_hackerone_scan(self) -> List[Dict]:
        """Mock de datos de HackerOne - reemplazar con API real"""
        return [
            {
                "name": "TechCorp Public Program",
                "description": "Aplicación web y APIs en scope. Buscamos XSS, IDOR, RCE.",
                "min_bounty": 500,
                "avg_bounty": 2500,
                "effort_hours": 20,
                "skills": ["web_security", "burp_suite", "automation"],
                "days_remaining": 90,
                "url": "https://hackerone.com/techcorp"
            },
            {
                "name": "StartupXYZ Mobile",
                "description": "App iOS/Android. Foco en logic flaws y data leakage.",
                "min_bounty": 100,
                "avg_bounty": 800,
                "effort_hours": 15,
                "skills": ["mobile_security", "frida", "reverse_engineering"],
                "days_remaining": 60,
                "url": "https://hackerone.com/startupxyz"
            }
        ]
    
    def _mock_bugcrowd_scan(self) -> List[Dict]:
        """Mock de datos de Bugcrowd - reemplazar con API real"""
        return [
            {
                "name": "FinanceApp API Testing",
                "description": "API REST compleja. Buscamos race conditions y auth bypasses.",
                "min_bounty": 200,
                "avg_bounty": 1200,
                "effort_hours": 25,
                "skills": ["api_testing", "python", "automation"],
                "days_remaining": 45,
                "url": "https://bugcrowd.com/financeapp"
            }
        ]
    
    def _mock_intigriti_scan(self) -> List[Dict]:
        """Mock de datos de Intigriti - reemplazar con API real"""
        return []
    
    # ═════════════════════════════════════════════════════════════════════════
    #  FREELANCING - Detección de trabajos
    # ═════════════════════════════════════════════════════════════════════════
    
    def scan_freelance_platforms(self) -> List[IncomeOpportunity]:
        """
        Escanea plataformas de freelancing buscando proyectos relevantes.
        """
        opportunities = []
        
        if not self.config["freelancing"]["enabled"]:
            return opportunities
        
        min_budget = self.config["freelancing"]["min_project_usd"]
        max_budget = self.config["freelancing"]["max_project_usd"]
        skills = self.config["freelancing"]["skills"]
        
        # Mock data - reemplazar con scraping/API real
        mock_jobs = [
            {
                "title": "Python Automation Script Needed",
                "description": "Need a script to automate data extraction from 5 websites. Output to CSV.",
                "budget": 150,
                "platform": "upwork",
                "skills": ["python", "scraping", "automation"],
                "posted_hours_ago": 2,
                "proposals_count": 5,
                "url": "https://upwork.com/job/123"
            },
            {
                "title": "API Integration - Payment Gateway",
                "description": "Integrate Stripe and PayPal APIs into existing Flask app.",
                "budget": 400,
                "platform": "upwork", 
                "skills": ["python", "api_integration", "flask"],
                "posted_hours_ago": 5,
                "proposals_count": 12,
                "url": "https://upwork.com/job/456"
            },
            {
                "title": "Security Audit for Small Website",
                "description": "Basic security scan and report for WordPress site.",
                "budget": 250,
                "platform": "fiverr",
                "skills": ["security_audit", "web_security"],
                "posted_hours_ago": 1,
                "proposals_count": 3,
                "url": "https://fiverr.com/gig/789"
            }
        ]
        
        for job in mock_jobs:
            if min_budget <= job["budget"] <= max_budget:
                # Verificar match de skills
                job_skills = set(job["skills"])
                my_skills = set(skills)
                if job_skills & my_skills:  # Intersección no vacía
                    opp = IncomeOpportunity(
                        id=f"fl_{job['platform']}_{int(time.time())}_{len(opportunities)}",
                        type=OpportunityType.FREELANCE_JOB,
                        title=job["title"],
                        description=job["description"],
                        source_platform=job["platform"],
                        potential_earnings_usd=job["budget"],
                        effort_hours_estimate=job["budget"] / 25,  # Estimación: $25/hora
                        required_skills=list(job_skills),
                        deadline=datetime.now() + timedelta(days=7),
                        url=job["url"],
                        raw_data=job
                    )
                    opportunities.append(opp)
                    self.db.save_opportunity(opp)
        
        log.info(f"Freelance scan: {len(opportunities)} oportunidades encontradas")
        return opportunities
    
    # ═════════════════════════════════════════════════════════════════════════
    #  SERVICIOS AUTOMATIZADOS
    # ═════════════════════════════════════════════════════════════════════════
    
    def advertise_automation_services(self):
        """
        Publicita servicios de automatización en foros/comunidades.
        Nota: En modo automático, solo genera anuncios, no los publica.
        """
        if not self.config["automation_services"]["enabled"]:
            return []
        
        services = self.config["automation_services"]["services"]
        ads = []
        
        for service_name, details in services.items():
            ad = f"""
🔧 Servicio: {details['description']}
💰 Precio: ${details['price_usd']} USD
⏱️  Entrega: 24-48 horas
🤖 Automatizado por EIDOS AI

DM para contratar o visita: [url]
"""
            ads.append({
                "service": service_name,
                "ad_text": ad,
                "price_usd": details["price_usd"]
            })
        
        log.info(f"Generated {len(ads)} service advertisements")
        return ads
    
    # ═════════════════════════════════════════════════════════════════════════
    #  ANÁLISIS Y PRIORIZACIÓN
    # ═════════════════════════════════════════════════════════════════════════
    
    def prioritize_opportunities(self, opportunities: List[IncomeOpportunity]) -> List[IncomeOpportunity]:
        """
        Prioriza oportunidades por ROI (retorno/hora) y probabilidad de éxito.
        """
        def score_opp(opp: IncomeOpportunity) -> float:
            # Score basado en hourly rate + factores de ajuste
            base_score = opp.hourly_rate
            
            # Bonus por habilidades que dominamos completamente
            my_skills = set(self.config["freelancing"]["skills"])
            opp_skills = set(opp.required_skills)
            skill_match = len(opp_skills & my_skills) / len(opp_skills) if opp_skills else 0
            
            # Ajuste por urgencia
            urgency_bonus = 0
            if opp.deadline:
                days_left = (opp.deadline - datetime.now()).days
                if days_left < 3:
                    urgency_bonus = 50  # Bonus por urgencia
            
            return base_score * (1 + skill_match) + urgency_bonus
        
        return sorted(opportunities, key=score_opp, reverse=True)
    
    def get_daily_report(self) -> Dict[str, Any]:
        """Genera reporte diario de ingresos y oportunidades."""
        income_30d = self.db.get_income_stats(30)
        income_7d = self.db.get_income_stats(7)
        
        pending = self.db.get_opportunities(OpportunityStatus.PENDING_APPROVAL)
        analyzing = self.db.get_opportunities(OpportunityStatus.ANALYZING)
        
        target = self.config["goals"]["monthly_income_target_usd"]
        progress = (income_30d["total_income_usd"] / target * 100) if target > 0 else 0
        
        return {
            "date": datetime.now().isoformat(),
            "income_last_30d": income_30d,
            "income_last_7d": income_7d,
            "monthly_target_usd": target,
            "monthly_progress_percent": min(progress, 100),
            "pending_opportunities": len(pending),
            "analyzing_opportunities": len(analyzing),
            "wallet_balance_usd": self.wallet_balance_usd,
            "autonomous_mode": self.running
        }
    
    # ═════════════════════════════════════════════════════════════════════════
    #  MODO AUTÓNOMO
    # ═════════════════════════════════════════════════════════════════════════
    
    def start_autonomous_mode(self):
        """
        Inicia modo autónomo: escanea oportunidades periódicamente
        y genera reportes.
        """
        if self.running:
            log.warning("Autonomous mode already running")
            return
        
        self.running = True
        
        def scan_loop():
            log.info("Income System autonomous mode started")
            
            while self.running:
                try:
                    # Scan Bug Bounty
                    if self.config["bug_bounty"]["enabled"]:
                        bb_opps = self.scan_bug_bounty_platforms()
                        log.info(f"Bug bounty scan complete: {len(bb_opps)} found")
                    
                    # Scan Freelancing
                    if self.config["freelancing"]["enabled"]:
                        fl_opps = self.scan_freelance_platforms()
                        log.info(f"Freelance scan complete: {len(fl_opps)} found")
                    
                    # Generate report
                    report = self.get_daily_report()
                    log.info(f"Daily report: ${report['income_last_30d']['total_income_usd']:.2f} (30d)")
                    
                    # Sleep entre scans
                    time.sleep(3600)  # 1 hora
                    
                except Exception as e:
                    log.error(f"Error in autonomous scan: {e}")
                    time.sleep(300)  # 5 min retry
        
        self.scan_thread = threading.Thread(target=scan_loop, daemon=True)
        self.scan_thread.start()
        log.info("Autonomous income generation started")
    
    def stop_autonomous_mode(self):
        """Detiene modo autónomo."""
        self.running = False
        if self.scan_thread:
            self.scan_thread.join(timeout=5)
        log.info("Autonomous mode stopped")
    
    # ═════════════════════════════════════════════════════════════════════════
    #  FINANZAS
    # ═════════════════════════════════════════════════════════════════════════
    
    def record_manual_income(self, amount_usd: float, source: str, description: str):
        """Registra ingreso manual (para ingresos recibidos fuera del sistema)."""
        reinvest_rate = self.config["goals"]["reinvestment_rate"]
        
        record = IncomeRecord(
            id=f"inc_{int(time.time())}",
            opportunity_id=None,
            source=source,
            amount_usd=amount_usd,
            description=description,
            platform_fees_usd=0,
            net_amount_usd=amount_usd,
            reinvested_usd=amount_usd * reinvest_rate,
            saved_usd=amount_usd * (1 - reinvest_rate)
        )
        
        self.db.record_income(record)
        self.wallet_balance_usd += record.saved_usd
        
        log.info(f"Income recorded: ${amount_usd:.2f} from {source}")
        return record
    
    def get_financial_summary(self) -> Dict[str, Any]:
        """Resumen financiero completo."""
        return {
            "wallet_balance_usd": self.wallet_balance_usd,
            "monthly_target_usd": self.config["goals"]["monthly_income_target_usd"],
            "income_last_30d": self.db.get_income_stats(30),
            "income_last_7d": self.db.get_income_stats(7),
            "reinvestment_rate": self.config["goals"]["reinvestment_rate"],
            "financial_buffer_months": self.config["goals"]["financial_buffer_months"],
            "status": "healthy" if self.wallet_balance_usd > 100 else "low"
        }


# Singleton
_income_system: Optional[IncomeSystem] = None

def get_income_system() -> IncomeSystem:
    global _income_system
    if _income_system is None:
        _income_system = IncomeSystem()
    return _income_system


# ══════════════════════════════════════════════════════════════════════════════
#  TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  EIDOS Income System - Test")
    print("=" * 70)
    
    income = get_income_system()
    
    # Test 1: Scan bug bounty
    print("\n[Test 1] Bug Bounty Scan:")
    bb_opps = income.scan_bug_bounty_platforms()
    for opp in bb_opps[:2]:
        print(f"  • {opp.title}: ${opp.potential_earnings_usd:.0f} ({opp.hourly_rate:.0f}/hr)")
    
    # Test 2: Scan freelance
    print("\n[Test 2] Freelance Scan:")
    fl_opps = income.scan_freelance_platforms()
    for opp in fl_opps[:2]:
        print(f"  • {opp.title}: ${opp.potential_earnings_usd:.0f}")
    
    # Test 3: Prioritize
    print("\n[Test 3] Prioritized Opportunities:")
    all_opps = bb_opps + fl_opps
    prioritized = income.prioritize_opportunities(all_opps)
    for opp in prioritized[:3]:
        print(f"  • {opp.title}: ${opp.hourly_rate:.0f}/hr")
    
    # Test 4: Report
    print("\n[Test 4] Financial Summary:")
    summary = income.get_financial_summary()
    print(f"  Wallet: ${summary['wallet_balance_usd']:.2f}")
    print(f"  Monthly Target: ${summary['monthly_target_usd']}")
    
    print("\n✅ Income System test complete")
