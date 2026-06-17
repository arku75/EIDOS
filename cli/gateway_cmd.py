"""
Gateway CLI - Comandos para gestionar el gateway EIDOS
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

# Añadir parent al path
sys.path.insert(0, str(Path(__file__).parent.parent))

from gateway.run import start_gateway, GatewayConfig
from gateway.cron.scheduler import get_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)

def gateway_cli():
    """Entry point para eidos-gateway"""
    parser = argparse.ArgumentParser(description="EIDOS Gateway CLI")
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Start
    start_parser = subparsers.add_parser("start", help="Start gateway")
    start_parser.add_argument("--port", type=int, default=18789)
    start_parser.add_argument("--host", default="127.0.0.1")
    start_parser.add_argument("--platforms", default="local", help="Comma-separated platforms")
    
    # Stop
    subparsers.add_parser("stop", help="Stop gateway")
    
    # Status
    subparsers.add_parser("status", help="Show gateway status")
    
    # Cron
    cron_parser = subparsers.add_parser("cron", help="Manage cron jobs")
    cron_subparsers = cron_parser.add_subparsers(dest="cron_cmd")
    
    cron_add = cron_subparsers.add_parser("add", help="Add cron job")
    cron_add.add_argument("--name", required=True)
    cron_add.add_argument("--prompt", required=True)
    cron_add.add_argument("--schedule", required=True, help="Cron format */5 * * * *")
    cron_add.add_argument("--deliver", default="local")
    
    cron_list = cron_subparsers.add_parser("list", help="List cron jobs")
    cron_del = cron_subparsers.add_parser("remove", help="Remove cron job")
    cron_del.add_argument("job_id")
    
    args = parser.parse_args()
    
    if args.command == "start":
        config = GatewayConfig(
            port=args.port,
            host=args.host,
            enabled_platforms=args.platforms.split(","),
        )
        print(f"🚀 Starting EIDOS Gateway on {args.host}:{args.port}")
        print(f"   Platforms: {config.enabled_platforms}")
        
        # Start cron scheduler
        scheduler = get_scheduler()
        scheduler.start()
        
        success = asyncio.run(start_gateway(config))
        sys.exit(0 if success else 1)
        
    elif args.command == "stop":
        print("🛑 Stopping gateway...")
        get_scheduler().stop()
        # TODO: Implementar stop graceful
        sys.exit(0)
        
    elif args.command == "status":
        print("📊 Gateway Status")
        print("   TODO: Implement status check")
        sys.exit(0)
        
    elif args.command == "cron":
        scheduler = get_scheduler()
        
        if args.cron_cmd == "add":
            job = scheduler.add_job(
                name=args.name,
                prompt=args.prompt,
                schedule=args.schedule,
                deliver=args.deliver,
            )
            print(f"✅ Added cron job: {job.job_id}")
            print(f"   Name: {job.name}")
            print(f"   Schedule: {job.schedule}")
            print(f"   Next run: {job.next_run}")
            
        elif args.cron_cmd == "list":
            jobs = scheduler.list_jobs()
            if not jobs:
                print("No cron jobs configured")
            else:
                print(f"📅 {len(jobs)} cron job(s):")
                for job in jobs:
                    status = "✅" if job.enabled else "⏸️"
                    print(f"   {status} {job.name} ({job.schedule}) - Next: {job.next_run}")
                    
        elif args.cron_cmd == "remove":
            if scheduler.remove_job(args.job_id):
                print(f"✅ Removed job: {args.job_id}")
            else:
                print(f"❌ Job not found: {args.job_id}")
                sys.exit(1)
        else:
            cron_parser.print_help()
    else:
        parser.print_help()

if __name__ == "__main__":
    gateway_cli()
