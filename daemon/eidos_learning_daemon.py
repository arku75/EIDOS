#!/usr/bin/env python3
"""
EIDOS Learning Daemon - Demonio de Aprendizaje 24/7
===================================================

Este daemon corre continuamente en background y hace que EIDOS aprenda sin parar.

Corre como servicio systemd y nunca se detiene.
"""

import os
import sys
import time
import signal
import logging
from pathlib import Path

# Agregar al path
EIDOS_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(EIDOS_ROOT))

from core.continuous_learner import get_continuous_learner, Priority

# Logging
# Systemd ya maneja stdout/stderr, solo necesitamos StreamHandler
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [Learning Daemon] %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Control de señales
running = True


def signal_handler(signum, frame):
    """Handler para señales de terminación"""
    global running
    logger.info(f"📢 Señal recibida: {signum}, deteniendo gracefully...")
    running = False


# Registrar handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def main():
    """Función principal del daemon"""
    logger.info("=" * 70)
    logger.info("🎓 EIDOS LEARNING DAEMON INICIADO")
    logger.info("=" * 70)
    logger.info("")
    logger.info("🔄 EIDOS aprenderá continuamente 24/7")
    logger.info("🎯 Fuentes: YouTube, PDFs, Web, GitHub, etc.")
    logger.info("♾️  NUNCA PARARÁ DE APRENDER")
    logger.info("")
    logger.info("=" * 70)

    # Obtener learner
    learner = get_continuous_learner()

    # Agregar fuentes iniciales de aprendizaje
    logger.info("\n📚 Agregando fuentes de aprendizaje iniciales...\n")

    # Videos de Rust (ejemplo)
    learner.add_youtube_video(
        "https://www.youtube.com/watch?v=5C_HPTJg5ek",
        title="Rust Crash Course",
        priority=Priority.HIGH,
        tags=['rust', 'tutorial']
    )

    # Documentación oficial
    learner.add_web_article(
        "https://doc.rust-lang.org/book/",
        title="The Rust Programming Language Book",
        priority=Priority.MEDIUM,
        tags=['rust', 'documentation']
    )

    learner.add_web_article(
        "https://fastapi.tiangolo.com/",
        title="FastAPI Documentation",
        priority=Priority.MEDIUM,
        tags=['python', 'fastapi', 'documentation']
    )

    # Repositorios interesantes
    learner.add_github_repo(
        "https://github.com/tokio-rs/tokio",
        title="Tokio - Async Runtime for Rust",
        priority=Priority.HIGH,
        tags=['rust', 'async', 'tokio']
    )

    # Iniciar aprendizaje continuo
    learner.start_learning()

    logger.info("\n✅ Aprendizaje continuo iniciado")
    logger.info(f"📊 Stats iniciales: {learner.get_stats()}\n")

    # Loop principal - mantener vivo y monitorear
    report_interval = 300  # Reportar cada 5 minutos
    last_report = time.time()

    global running
    while running:
        try:
            current_time = time.time()

            # Reportar stats periódicamente
            if current_time - last_report >= report_interval:
                stats = learner.get_stats()

                logger.info("\n" + "="*70)
                logger.info("📊 REPORTE DE APRENDIZAJE")
                logger.info("="*70)
                logger.info(f"✅ Tareas completadas: {stats['completed']}")
                logger.info(f"❌ Tareas fallidas: {stats['failed']}")
                logger.info(f"📋 Cola pendiente: {stats['queue_size']}")
                logger.info(f"📈 Tasa de éxito: {stats['success_rate']:.1f}%")
                logger.info(f"🔄 Estado: {'Activo' if stats['is_running'] else 'Pausado'}")
                logger.info("="*70 + "\n")

                last_report = current_time

            # Dormir un poco
            time.sleep(10)

        except Exception as e:
            logger.error(f"❌ Error en loop principal: {e}")
            import traceback
            logger.error(traceback.format_exc())
            time.sleep(30)  # Esperar más en caso de error

    # Shutdown gracefully
    logger.info("\n🛑 Deteniendo aprendizaje...")
    learner.stop_learning()

    # Stats finales
    final_stats = learner.get_stats()
    logger.info("\n" + "="*70)
    logger.info("📊 ESTADÍSTICAS FINALES")
    logger.info("="*70)
    logger.info(f"Total de tareas: {final_stats['total_tasks']}")
    logger.info(f"Completadas: {final_stats['completed']}")
    logger.info(f"Fallidas: {final_stats['failed']}")
    logger.info(f"Tasa de éxito: {final_stats['success_rate']:.1f}%")
    logger.info("="*70)

    logger.info("\n👋 EIDOS Learning Daemon finalizado\n")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        logger.critical(f"💥 Error crítico: {e}")
        import traceback
        logger.critical(traceback.format_exc())
        sys.exit(1)
