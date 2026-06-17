"""
EIDOS Trading System - Economía P2P entre Agentes
===========================================

Sistema de trading P2P que permite a los agentes intercambiar tokens,
servicios y recursos entre ellos.
"""

import random
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum


class TradeStatus(Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COMPLETED = "completed"
    EXPIRED = "expired"


class TradeType(Enum):
    TOKENS_FOR_SERVICE = "tokens_for_service"
    TOKEN_SWAP = "token_swap"
    COLLABORATION = "collaboration"
    RESOURCE_EXCHANGE = "resource_exchange"


@dataclass
class Trade:
    """Una oferta de trade entre agentes"""
    id: str
    from_agent: str
    to_agent: str
    trade_type: TradeType
    offer: Dict[str, Any]  # Lo que ofrece
    request: Dict[str, Any]  # Lo que pide
    status: TradeStatus = TradeStatus.PENDING
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    completed_at: Optional[float] = None
    
    def __post_init__(self):
        if not self.id:
            self.id = f"trade_{int(time.time())}_{random.randint(1000,9999)}"
        if self.expires_at is None:
            self.expires_at = self.created_at + 300  # 5 minutos default
    
    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at
    
    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at


class TradingSystem:
    """Sistema central de trading P2P"""
    
    def __init__(self):
        self.active_trades: Dict[str, Trade] = {}
        self.trade_history: List[Dict] = []
        self.agent_reputations: Dict[str, float] = {}  # Score 0-100
        self.price_oracle = {
            "code_review": 50,
            "bug_fix": 100,
            "analysis": 75,
            "design": 80,
            "deploy": 150,
            "consultation": 30
        }
    
    def create_trade_offer(self, from_agent: str, to_agent: str, 
                          trade_type: TradeType,
                          offer: Dict[str, Any], 
                          request: Dict[str, Any]) -> Optional[Trade]:
        """Crea una oferta de trade"""
        
        trade = Trade(
            id=f"T{int(time.time())}{random.randint(100,999)}",
            from_agent=from_agent,
            to_agent=to_agent,
            trade_type=trade_type,
            offer=offer,
            request=request
        )
        
        self.active_trades[trade.id] = trade
        return trade
    
    def accept_trade(self, trade_id: str) -> bool:
        """Acepta un trade pendiente"""
        trade = self.active_trades.get(trade_id)
        if not trade or trade.status != TradeStatus.PENDING:
            return False
        
        if trade.is_expired:
            trade.status = TradeStatus.EXPIRED
            return False
        
        trade.status = TradeStatus.ACCEPTED
        trade.completed_at = time.time()
        
        # Registrar en historial
        self.trade_history.append({
            "trade_id": trade_id,
            "from": trade.from_agent,
            "to": trade.to_agent,
            "type": trade.trade_type.value,
            "offer": trade.offer,
            "request": trade.request,
            "completed_at": trade.completed_at
        })
        
        # Actualizar reputaciones
        self._update_reputation(trade.from_agent, 5)
        self._update_reputation(trade.to_agent, 5)
        
        return True
    
    def reject_trade(self, trade_id: str) -> bool:
        """Rechaza un trade"""
        trade = self.active_trades.get(trade_id)
        if not trade or trade.status != TradeStatus.PENDING:
            return False
        
        trade.status = TradeStatus.REJECTED
        return True
    
    def get_agent_trades(self, agent_id: str, status: TradeStatus = None) -> List[Trade]:
        """Obtiene trades de un agente"""
        trades = []
        for trade in self.active_trades.values():
            if trade.from_agent == agent_id or trade.to_agent == agent_id:
                if status is None or trade.status == status:
                    trades.append(trade)
        return trades
    
    def get_market_rate(self, service: str) -> float:
        """Obtiene el precio de mercado para un servicio"""
        base = self.price_oracle.get(service, 50)
        # Añadir variación del mercado
        variation = random.uniform(-0.1, 0.1)
        return base * (1 + variation)
    
    def suggest_trade(self, agent_id: str, other_agents: List[str]) -> Optional[Trade]:
        """Sugiere un trade basado en necesidades del agente"""
        if not other_agents:
            return None
        
        # Seleccionar agente con mejor reputación
        best_agent = max(other_agents, key=lambda a: self.agent_reputations.get(a, 50))
        
        # Determinar tipo de trade
        trade_types = list(TradeType)
        trade_type = random.choice(trade_types)
        
        if trade_type == TradeType.TOKENS_FOR_SERVICE:
            service = random.choice(list(self.price_oracle.keys()))
            price = self.get_market_rate(service)
            
            offer = {"tokens": price * 0.8}  # 20% descuento
            request = {"service": service, "details": f"{service} profesional"}
            
        elif trade_type == TradeType.TOKEN_SWAP:
            amount = random.randint(50, 200)
            offer = {"tokens": amount}
            request = {"tokens": amount * 1.1}  # 10% comisión
            
        elif trade_type == TradeType.COLLABORATION:
            offer = {"collaboration": "co_programming", "hours": 2}
            request = {"collaboration": "code_review", "hours": 2}
            
        else:  # RESOURCE_EXCHANGE
            resources = ["cpu_time", "memory", "storage"]
            offer = {"resource": random.choice(resources), "amount": 100}
            request = {"resource": random.choice(resources), "amount": 100}
        
        return self.create_trade_offer(agent_id, best_agent, trade_type, offer, request)
    
    def _update_reputation(self, agent_id: str, delta: float):
        """Actualiza reputación de un agente"""
        current = self.agent_reputations.get(agent_id, 50)
        self.agent_reputations[agent_id] = max(0, min(100, current + delta))
    
    def get_reputation(self, agent_id: str) -> float:
        """Obtiene reputación de un agente"""
        return self.agent_reputations.get(agent_id, 50)
    
    def cleanup_expired(self):
        """Limpia trades expirados"""
        expired = []
        for trade_id, trade in self.active_trades.items():
            if trade.is_expired and trade.status == TradeStatus.PENDING:
                trade.status = TradeStatus.EXPIRED
                expired.append(trade_id)
        
        for tid in expired:
            del self.active_trades[tid]
        
        return len(expired)
    
    def get_stats(self) -> Dict:
        """Estadísticas del sistema de trading"""
        return {
            "active_trades": len([t for t in self.active_trades.values() if t.status == TradeStatus.PENDING]),
            "completed_trades": len([h for h in self.trade_history]),
            "total_volume": sum(h["offer"].get("tokens", 0) for h in self.trade_history),
            "avg_reputation": sum(self.agent_reputations.values()) / max(len(self.agent_reputations), 1),
            "price_oracle": self.price_oracle
        }


# Singleton
_trading_system = None

def get_trading_system() -> TradingSystem:
    global _trading_system
    if _trading_system is None:
        _trading_system = TradingSystem()
    return _trading_system


if __name__ == "__main__":
    ts = get_trading_system()
    print("=" * 60)
    print("  EIDOS Trading System")
    print("=" * 60)
    print(f"  Precios de referencia:")
    for service, price in ts.price_oracle.items():
        print(f"    • {service}: {price} tokens")
