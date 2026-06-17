#!/bin/bash
# Launcher del servidor HexStrike AI para EIDOS [S88 CARNE]
# Puerto 8889 para no colisionar con Colony (:7777), Bridge (:8003), WebPanel (:8080)

cd /home/ser/EIDOS/plugins/hexstrike/hexstrike-ai-master
exec python3 hexstrike_server.py --port 8889
