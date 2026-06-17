#!/bin/bash
#
# Instalador del Sistema de Aprendizaje Continuo de EIDOS
# =========================================================

set -e

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║     EIDOS Continuous Learning System - Instalador             ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# 1. Instalar dependencias Python
echo "📦 Instalando dependencias Python..."
pip3 install --user --break-system-packages yt-dlp PyPDF2 beautifulsoup4 requests 2>/dev/null || \
pip3 install --user yt-dlp PyPDF2 beautifulsoup4 requests 2>/dev/null || \
echo "⚠️  Algunas dependencias pueden estar ya instaladas"

echo "✅ Dependencias instaladas"
echo ""

# 2. Verificar yt-dlp
echo "🔍 Verificando yt-dlp..."
if command -v yt-dlp &> /dev/null; then
    echo "✅ yt-dlp encontrado: $(yt-dlp --version)"
else
    echo "⚠️  yt-dlp no encontrado en PATH, instalando..."
    pip3 install --user --break-system-packages yt-dlp 2>/dev/null || \
    pip3 install --user yt-dlp 2>/dev/null
    echo "✅ yt-dlp instalado"
fi
echo ""

# 3. Crear directorios necesarios
echo "📁 Creando directorios..."
mkdir -p ~/.eidos/{logs,downloads,transcripts,extracted_knowledge}
echo "✅ Directorios creados"
echo ""

# 4. Hacer ejecutables los scripts
echo "🔧 Configurando permisos..."
chmod +x /home/ser/EIDOS/My_Gpt/eidos_learning_daemon.py
chmod +x /home/ser/EIDOS/My_Gpt/core/continuous_learner.py
echo "✅ Permisos configurados"
echo ""

# 5. Instalar servicio systemd (opcional)
echo "🔄 ¿Instalar como servicio systemd? (Aprendizaje 24/7 automático)"
echo "   Esto hará que EIDOS aprenda continuamente incluso sin VSCode abierto"
read -p "   Instalar servicio? (s/N): " install_service

if [[ "$install_service" =~ ^[Ss]$ ]]; then
    echo ""
    echo "📌 Instalando servicio systemd..."

    if [ "$EUID" -ne 0 ]; then
        echo "   Se necesitan permisos de root para instalar el servicio"
        echo "   Ejecutando con sudo..."
        sudo cp /home/ser/EIDOS/My_Gpt/eidos-learning.service /etc/systemd/system/
        sudo systemctl daemon-reload
        sudo systemctl enable eidos-learning.service
        sudo systemctl start eidos-learning.service

        echo ""
        echo "✅ Servicio instalado y iniciado"
        echo ""
        echo "📊 Estado del servicio:"
        sudo systemctl status eidos-learning.service --no-pager | head -15
    else
        cp /home/ser/EIDOS/My_Gpt/eidos-learning.service /etc/systemd/system/
        systemctl daemon-reload
        systemctl enable eidos-learning.service
        systemctl start eidos-learning.service

        echo "✅ Servicio instalado y iniciado"
    fi
else
    echo "⏭️  Instalación de servicio omitida"
    echo "   Puedes iniciar manualmente con:"
    echo "   python3 /home/ser/EIDOS/My_Gpt/eidos_learning_daemon.py"
fi

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║              ✅ INSTALACIÓN COMPLETADA                         ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "🎓 EIDOS Continuous Learning System está listo"
echo ""
echo "📝 Cómo usar:"
echo ""
echo "   1. Agregar fuentes de aprendizaje:"
echo "      python3 -c '"
echo "from My_Gpt.core.continuous_learner import get_continuous_learner, Priority"
echo "learner = get_continuous_learner()"
echo "learner.add_youtube_video(\"URL\", \"Título\", Priority.HIGH)"
echo "learner.add_pdf_book(\"/ruta/libro.pdf\", \"Título\")"
echo "learner.add_web_article(\"URL\", \"Título\")"
echo "'"
echo ""
echo "   2. Ver logs:"
echo "      tail -f ~/.eidos/logs/learning_daemon.log"
echo ""
echo "   3. Ver contenido aprendido:"
echo "      cat ~/.eidos/learned_content.json | python3 -m json.tool"
echo ""
echo "   4. Comandos del servicio (si instalaste systemd):"
echo "      sudo systemctl status eidos-learning"
echo "      sudo systemctl restart eidos-learning"
echo "      sudo journalctl -u eidos-learning -f"
echo ""
echo "🎯 EIDOS ahora aprende 24/7 de:"
echo "   📹 Videos de YouTube"
echo "   📚 Libros PDF"
echo "   🌐 Artículos web"
echo "   💻 Repositorios GitHub"
echo ""
echo "♾️  NUNCA PARARÁ DE APRENDER 🚀"
echo ""
