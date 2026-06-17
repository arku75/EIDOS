# ─────────────────────────────────────────────────────────────────────────────
# install_clone_windows.ps1 — Instalador clon EIDOS para Windows 10/11
# ─────────────────────────────────────────────────────────────────────────────
# Línea disciplinada:
#   • Banner + consentimiento explícito ANTES de tocar disco.
#   • NO abre puertos del router. NO instala remote-access genérico.
#   • NO toca cookies ni keystore del navegador.
#   • Imprime claramente a qué hub se conecta y cómo desinstalar.
#   • Soporta -DryRun para inspección previa.
#
# Uso típico (one-liner desde el hub público):
#   irm https://xxx-aleatorio.trycloudflare.com/install.ps1 | iex
#
# Uso manual (script descargado):
#   .\install_clone_windows.ps1 -HubUrl https://hub/  [-DryRun] [-Force]
#                               [-NoEnroll] [-SkipService] [-EnrollToken xxx]
# ─────────────────────────────────────────────────────────────────────────────

# __PARAM_BLOCK_START__
[CmdletBinding()]
param(
    [string]$HubUrl = '__HUB_URL__',
    [string]$EnrollToken = '__ENROLL_TOKEN__',
    [string]$FriendlyName = $null,
    [switch]$DryRun,
    [switch]$Force,
    [switch]$NoEnroll,
    [switch]$SkipService
)
# __PARAM_BLOCK_END__

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ── Rutas ──────────────────────────────────────────────────────────────────
$CloneHome  = Join-Path $env:USERPROFILE '.eidos-clone'
$EidosDir   = Join-Path $env:USERPROFILE '.eidos'
$ClonePriv  = Join-Path $EidosDir 'clone'
$HubLocal   = Join-Path $EidosDir 'hub'
$VenvDir    = Join-Path $CloneHome '.venv'
$VenvPy     = Join-Path $VenvDir 'Scripts\python.exe'
$BinDir     = Join-Path $CloneHome 'bin'
$LogsDir    = Join-Path $CloneHome 'logs'
$CoreDir    = Join-Path $CloneHome 'core'

# ── Banner ─────────────────────────────────────────────────────────────────
function Show-Banner {
    Write-Host ""
    Write-Host "  ███████╗██╗██████╗  ██████╗ ███████╗" -ForegroundColor Cyan
    Write-Host "  ██╔════╝██║██╔══██╗██╔═══██╗██╔════╝" -ForegroundColor Cyan
    Write-Host "  █████╗  ██║██║  ██║██║   ██║███████╗" -ForegroundColor Cyan
    Write-Host "  ██╔══╝  ██║██║  ██║██║   ██║╚════██║" -ForegroundColor Cyan
    Write-Host "  ███████╗██║██████╔╝╚██████╔╝███████║" -ForegroundColor Cyan
    Write-Host "  ╚══════╝╚═╝╚═════╝  ╚═════╝ ╚══════╝" -ForegroundColor Cyan
    Write-Host "  Clone Installer for Windows  ·  v1.0" -ForegroundColor DarkCyan
    Write-Host ""
}

function Write-Step([string]$msg) { Write-Host "  → $msg" -ForegroundColor White }
function Write-Ok([string]$msg)   { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Warn2([string]$msg) { Write-Host "  ⚠ $msg" -ForegroundColor Yellow }
function Write-Err([string]$msg)  { Write-Host "  ✗ $msg" -ForegroundColor Red }
function Write-Hr   { Write-Host ("─" * 70) -ForegroundColor DarkGray }

Show-Banner
Write-Hr
Write-Host "  Hub:        $HubUrl" -ForegroundColor White
Write-Host "  Clone home: $CloneHome" -ForegroundColor White
Write-Host "  Identidad:  $ClonePriv" -ForegroundColor White
Write-Hr
Write-Host @"

  Este instalador va a:
    1) Crear $CloneHome (venv Python + módulos del clon).
    2) Generar un par Ed25519 propio en $ClonePriv
       (la clave PRIVADA NUNCA sale de este equipo).
    3) Pedirte un TOKEN de enrollment (te lo da SER, single-use, TTL 15 min).
    4) Registrar este equipo contra el hub EIDOS — recibirás un clone_id.
    5) Opcional: crear una Tarea Programada para el túnel cuando esté lista.

  Lo que NO hace (línea disciplinada):
    • NO abre puertos en tu router. Los túneles salen outbound.
    • NO instala remote-access genérico (VNC/RDP/AnyDesk).
    • NO lee cookies de tu navegador.
    • NO se actualiza solo.

  Para retirar el clon más adelante:
    powershell -File "$CloneHome\uninstall.ps1"

"@ -ForegroundColor Gray

if ($HubUrl -eq '__HUB_URL__') {
    Write-Err "HubUrl no proporcionado. Usa -HubUrl https://hub-publico/ o llama desde el orquestador del hub."
    exit 2
}

# ── Guard: ¿es la máquina del HUB? ─────────────────────────────────────────
if ((Test-Path $HubLocal) -and (-not $Force)) {
    Write-Err "Este equipo parece ser el HUB EIDOS de SER ($HubLocal existe)."
    Write-Err "El instalador de clon no debe correr aquí — pisaría tus keys."
    Write-Err "Si REALMENTE quieres seguir (sandbox, repaso), añade -Force."
    exit 1
}

# ── Consentimiento explícito ───────────────────────────────────────────────
if (-not $DryRun) {
    $ans = Read-Host "  ¿Continuar con la instalación? [s/N]"
    if ($ans -notmatch '^[SsYy]$') {
        Write-Host "  Cancelado por el usuario."
        exit 0
    }
}

# ── Prereqs ────────────────────────────────────────────────────────────────
Write-Step "Verificando prerequisitos…"

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Err "python no encontrado en PATH. Instala Python 3.10+ desde python.org (marca 'Add to PATH')."
    exit 3
}
$pyVer = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
$pyOk  = & python -c "import sys; print('1' if sys.version_info >= (3,10) else '0')"
if ($pyOk -ne '1') {
    Write-Err "Python 3.10+ requerido (tienes $pyVer)."
    exit 3
}
Write-Ok "Python $pyVer en $($py.Source)"

$ssh = Get-Command ssh -ErrorAction SilentlyContinue
if (-not $ssh) {
    Write-Warn2 "ssh no encontrado en PATH. Settings → Apps → Optional Features → OpenSSH Client (necesario para el túnel)."
} else {
    $sshVer = (& ssh -V 2>&1) -join ''
    Write-Ok "OpenSSH disponible: $sshVer"
}

$sshKeygen = Get-Command ssh-keygen -ErrorAction SilentlyContinue
if (-not $sshKeygen) {
    Write-Err "ssh-keygen no encontrado en PATH (parte de OpenSSH Client)."
    exit 3
}

# ── DryRun: salir aquí ─────────────────────────────────────────────────────
if ($DryRun) {
    Write-Host ""
    Write-Host "  [DRY RUN] Plan:" -ForegroundColor Yellow
    Write-Host "    HubUrl       = $HubUrl"
    Write-Host "    CloneHome    = $CloneHome"
    Write-Host "    ClonePriv    = $ClonePriv"
    Write-Host "    NoEnroll     = $NoEnroll"
    Write-Host "    SkipService  = $SkipService"
    Write-Host ""
    Write-Host "  Para ejecutar de verdad: re-corre SIN -DryRun." -ForegroundColor Yellow
    exit 0
}

# ── Crear estructura ───────────────────────────────────────────────────────
Write-Step "Creando estructura en $CloneHome…"
foreach ($d in @($CloneHome, $CoreDir, $BinDir, $LogsDir, $EidosDir, $ClonePriv)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}

# Restringir permisos (sólo USER) — equivalente a chmod 700/600
function Restrict-AclToCurrentUser([string]$path) {
    try {
        $acl = Get-Acl -Path $path
        $acl.SetAccessRuleProtection($true, $false)  # disable inheritance, no copy
        $user = "$env:USERDOMAIN\$env:USERNAME"
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $user, 'FullControl',
            'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
        Set-Acl -Path $path -AclObject $acl
    } catch {
        Write-Warn2 "No pude restringir ACL de ${path}: $($_.Exception.Message)"
    }
}
Restrict-AclToCurrentUser $CloneHome
Restrict-AclToCurrentUser $ClonePriv

# ── Descargar módulos del hub vía HTTPS ────────────────────────────────────
Write-Step "Descargando módulos del clon desde el hub…"
$modules = @(
    'eidos_clone_agent.py',
    'eidos_tunnel.py',
    'eidos_command_channel.py',
    'eidos_owner_policy.py'
)
foreach ($m in $modules) {
    $url  = "$HubUrl/install/files/$m"
    $dest = Join-Path $CoreDir $m
    try {
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -TimeoutSec 30 -ErrorAction Stop
    } catch {
        Write-Err "Fallo descargando ${m}: $($_.Exception.Message)"
        exit 4
    }
}
'' | Set-Content -Path (Join-Path $CoreDir '__init__.py') -Encoding UTF8
Write-Ok "$($modules.Count) módulos descargados"

# clone_runner.py + requirements
foreach ($f in @('clone_runner.py', 'requirements_clone_minimal.txt')) {
    $url  = "$HubUrl/install/files/$f"
    $dest = Join-Path $CloneHome ($f -replace 'requirements_clone_minimal\.txt$', 'requirements.txt')
    try {
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -TimeoutSec 30 -ErrorAction Stop
    } catch {
        Write-Err "Fallo descargando ${f}: $($_.Exception.Message)"
        exit 4
    }
}
Write-Ok "Runner + requirements descargados"

# ── Crear venv + instalar deps ─────────────────────────────────────────────
Write-Step "Creando entorno virtual Python…"
& python -m venv $VenvDir
if (-not (Test-Path $VenvPy)) {
    Write-Err "venv no se creó correctamente en $VenvDir"
    exit 5
}
Write-Step "Instalando dependencias mínimas (cryptography, urllib3, certifi)…"
& $VenvPy -m pip install --quiet --upgrade pip
& $VenvPy -m pip install --quiet -r (Join-Path $CloneHome 'requirements.txt')
if ($LASTEXITCODE -ne 0) {
    Write-Err "pip install falló (código $LASTEXITCODE)"
    exit 6
}
Write-Ok "venv preparado en $VenvDir"

# ── Wrapper bin/eidos-clone.cmd ────────────────────────────────────────────
$wrapperCmd = @"
@echo off
"$VenvPy" "$CloneHome\clone_runner.py" %*
"@
$wrapperPath = Join-Path $BinDir 'eidos-clone.cmd'
[System.IO.File]::WriteAllText($wrapperPath, $wrapperCmd, [System.Text.UTF8Encoding]::new($false))
Write-Ok "Wrapper: $wrapperPath"

# ── Uninstaller ────────────────────────────────────────────────────────────
$uninstall = @"
# uninstall.ps1 — Retira el clon EIDOS de este equipo
`$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Write-Host '  → Parando Tarea Programada (si existe)…' -ForegroundColor White
schtasks /Delete /TN 'EIDOS Clone' /F 2>`$null | Out-Null
Write-Host '  → Borrando $CloneHome …' -ForegroundColor White
Remove-Item -Recurse -Force '$CloneHome'
Write-Host '  → Identidad cripto en $ClonePriv NO se borra automáticamente.' -ForegroundColor Yellow
Write-Host '    Si quieres retirarla del todo:' -ForegroundColor Yellow
Write-Host '       Remove-Item -Recurse -Force ''$ClonePriv''' -ForegroundColor Yellow
Write-Host '  ✓ Clon retirado.' -ForegroundColor Green
"@
$uninstallPath = Join-Path $CloneHome 'uninstall.ps1'
[System.IO.File]::WriteAllText($uninstallPath, $uninstall, [System.Text.UTF8Encoding]::new($false))
Write-Ok "Uninstaller: $uninstallPath"

# ── Inicializar identidad Ed25519 + SSH key ────────────────────────────────
Write-Step "Inicializando identidad criptográfica del clon (Ed25519 + SSH key)…"
& $VenvPy (Join-Path $CloneHome 'clone_runner.py') init | Out-Host
if ($LASTEXITCODE -ne 0) {
    Write-Err "init falló (código $LASTEXITCODE)"
    exit 7
}

# ── Wizard enrollment ──────────────────────────────────────────────────────
if (-not $NoEnroll) {
    Write-Host ""
    Write-Hr
    Write-Host "  ENROLLMENT" -ForegroundColor Cyan
    Write-Hr

    $enrollArgs = @((Join-Path $CloneHome 'clone_runner.py'), 'enroll', '--hub-url', $HubUrl)
    if ($EnrollToken) { $enrollArgs += @('--token', $EnrollToken) }
    if ($FriendlyName) { $enrollArgs += @('--name', $FriendlyName) }

    & $VenvPy @enrollArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "Enrollment falló o se canceló. Puedes reintentarlo más tarde:"
        Write-Host "     `"$wrapperPath`" enroll --hub-url $HubUrl"
    }
} else {
    Write-Host ""
    Write-Step "Enrollment OMITIDO (-NoEnroll). Cuando lo necesites:"
    Write-Host "     `"$wrapperPath`" enroll --hub-url $HubUrl"
}

# ── Scheduled Task (deshabilitada por defecto) ─────────────────────────────
if (-not $SkipService) {
    Write-Step "Creando Tarea Programada 'EIDOS Clone' (deshabilitada inicialmente)…"
    $taskCmd = "`"$VenvPy`" `"$CloneHome\clone_runner.py`" tunnel"
    # Borrar si existe previa
    schtasks /Delete /TN 'EIDOS Clone' /F 2>$null | Out-Null
    $created = schtasks /Create /TN 'EIDOS Clone' /TR $taskCmd /SC ONLOGON /RL LIMITED /F 2>&1
    if ($LASTEXITCODE -eq 0) {
        # Crear como deshabilitada (el túnel requiere configurar authorized_keys del hub primero)
        schtasks /Change /TN 'EIDOS Clone' /DISABLE 2>$null | Out-Null
        Write-Ok "Tarea creada (DESHABILITADA). Cuando el hub te haya asignado puerto:"
        Write-Host "     schtasks /Change /TN 'EIDOS Clone' /ENABLE" -ForegroundColor DarkGray
        Write-Host "     schtasks /Run    /TN 'EIDOS Clone'" -ForegroundColor DarkGray
    } else {
        Write-Warn2 "No pude crear la Tarea Programada: $created"
    }
}

# ── Mensaje final ──────────────────────────────────────────────────────────
Write-Host ""
Write-Hr
Write-Host "  ✓ Clon EIDOS instalado" -ForegroundColor Green
Write-Hr
Write-Host "  Comandos útiles:"
Write-Host "     status:       `"$wrapperPath`" status"
Write-Host "     re-enroll:    `"$wrapperPath`" enroll --hub-url $HubUrl"
Write-Host "     desinstalar:  powershell -File `"$uninstallPath`""
Write-Host ""
Write-Host "  Identidad cripto persistente:  $ClonePriv"
Write-Host "  Logs del clon:                 $LogsDir"
Write-Hr
Write-Host ""
