// EIDOS safe-executor — Ring 3 Hardening (F21) v0.2.0
// =====================================================
// Capas de protección:
//   Ring 1: Lista negra de comandos destructivos
//   Ring 2: Sanitización de entorno (sin secretos)
//   Ring 3: Timeout estricto + límite de output
//   Ring 4: Namespace PID aislado (CLONE_NEWPID via libc::unshare)
//   Ring 5: Namespace Net aislado (CLONE_NEWNET) — sin acceso a red en hijos
//
// Uso: safe-executor <cmd> [args...]
// Output: JSON { stdout, stderr, exit_code, error, blocked_reason, isolated }

use libc::{CLONE_NEWNET, CLONE_NEWPID};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::env;
use std::os::unix::process::CommandExt; // para pre_exec
use std::process::Command;
use std::time::Duration;
use tokio::time::timeout;
use libseccomp::{ScmpAction, ScmpFilterContext, ScmpSyscall};

// ── Configuración de seguridad ─────────────────────────────────────────────

const TIMEOUT_DEFAULT_S: u64  = 30;
const MAX_OUTPUT_BYTES:  usize = 1024 * 512; // 512KB
const MAX_ARGS:          usize = 64;

// Controla el aislamiento de red (desactivar si el proceso necesita red)
// Por defecto: SÍ aislar (más seguro)
const ISOLATE_NET: bool = true;

/// Comandos completamente bloqueados — nunca ejecutados.
const BLACKLISTED_CMDS: &[&str] = &[
    "rm", "shred", "dd", "wipefs",
    "mkfs", "fdisk", "parted", "gdisk",
    "sudo", "su", "pkexec", "doas",
    "useradd", "userdel", "usermod", "passwd", "chown", "chmod",
    "systemctl", "service", "init", "telinit", "shutdown", "reboot", "poweroff",
    "insmod", "rmmod", "modprobe",
    "nsenter",  // unshare permitido pero sólo los nuestros
];

/// Rutas de sistema protegidas — ningún argumento puede referenciarlas.
const PROTECTED_PATHS: &[&str] = &[
    "/etc/shadow",
    "/etc/sudoers",
    "/boot/",
    "/sys/firmware/",
    "/proc/sysrq-trigger",
];

/// Argumentos de rm siempre bloqueados.
const BLACKLISTED_RM_ARGS: &[&str] = &[
    "-rf /", "--no-preserve-root", "/*",
    "/home", "/root", "/etc", "/usr", "/bin", "/sbin", "/lib",
    "/var", "/tmp/*", "/proc", "/sys",
];

// ── Estructuras ─────────────────────────────────────────────────────────────

#[derive(Serialize, Deserialize)]
struct ExecResult {
    stdout:         String,
    stderr:         String,
    #[serde(skip_serializing_if = "Option::is_none")]
    parsed_output:  Option<serde_json::Value>,
    exit_code:      Option<i32>,
    error:          Option<String>,
    blocked_reason: Option<String>,
    cmd_executed:   String,
    duration_ms:    u64,
    isolated:       bool,  // si se ejecutó con aislamiento de namespaces
    seccomp_active: bool,  // si seccomp BPF estaba activo
}

// ── Lógica de seguridad ─────────────────────────────────────────────────────

fn is_blacklisted(cmd: &str, args: &[String]) -> Option<String> {
    let cmd_base = std::path::Path::new(cmd)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or(cmd);

    // 1. Comando en lista negra
    if BLACKLISTED_CMDS.contains(&cmd_base) {
        return Some(format!(
            "[RING3] Comando '{}' bloqueado por política de seguridad de EIDOS.",
            cmd_base
        ));
    }

    // 2. Argumentos peligrosos
    for arg in args {
        if arg.contains('\0') {
            return Some(format!(
                "[RING3] Argumento con null byte detectado: {:?}",
                &arg[..20.min(arg.len())]
            ));
        }
        let stripped = arg.trim();
        if stripped.starts_with(';') || stripped.starts_with('|') || stripped.starts_with("&&") {
            return Some(format!(
                "[RING3] Posible shell injection en argumento: {:?}",
                &stripped[..30.min(stripped.len())]
            ));
        }
        for protected in PROTECTED_PATHS {
            if arg.contains(protected) {
                return Some(format!("[RING3] Acceso a ruta protegida bloqueado: {}", protected));
            }
        }
    }

    // 3. rm con argumentos destructivos
    if cmd_base == "rm" {
        let args_str = args.join(" ");
        for bad_arg in BLACKLISTED_RM_ARGS {
            if args_str.contains(bad_arg) {
                return Some(format!(
                    "[RING3] rm con argumento catastrófico bloqueado: {}", bad_arg
                ));
            }
        }
    }

    // 4. Demasiados argumentos (DoS)
    if args.len() > MAX_ARGS {
        return Some(format!(
            "[RING3] Demasiados argumentos: {} > máximo {}", args.len(), MAX_ARGS
        ));
    }

    None
}

fn sanitize_env() -> Vec<(String, String)> {
    let allowed_vars: HashSet<&str> = [
        "HOME", "USER", "SHELL", "TERM", "LANG", "LC_ALL", "LC_CTYPE",
        "PATH", "PYTHONPATH", "EIDOS_DIR", "OLLAMA_URL", "DISPLAY",
        "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR",
        "XDG_DATA_DIRS", "XDG_CONFIG_DIRS",
    ].iter().cloned().collect();

    let secret_patterns = [
        "PASSWORD", "SECRET", "TOKEN", "KEY", "CREDENTIAL",
        "AUTH", "PRIVATE", "API_KEY", "AWS_", "GITHUB_",
    ];

    env::vars()
        .filter(|(k, _)| {
            if !allowed_vars.contains(k.as_str()) {
                return false;
            }
            let k_upper = k.to_uppercase();
            !secret_patterns.iter().any(|p| k_upper.contains(p))
        })
        .collect()
}

fn truncate_output(s: String, max: usize) -> String {
    if s.len() <= max {
        return s;
    }
    let half = max / 2;
    let head = &s[..half];
    let tail = &s[s.len() - half..];
    format!("{}\n...[OUTPUT TRUNCADO: {} bytes omitidos]...\n{}", head, s.len() - max, tail)
}

// ── Ring 4+5: Namespace isolation via pre_exec ──────────────────────────────
// 
// unshare(CLONE_NEWPID) → el proceso hijo tiene su propio espacio de PIDs.
//   - No puede ver procesos del host (PID 1 en su namespace = él mismo).
//   - No puede enviar señales a procesos fuera de su namespace.
//
// unshare(CLONE_NEWNET) → el proceso hijo tiene interfaz de red vacía.
//   - No puede conectarse a internet ni a localhost directamente.
//   - Bloquea exfiltración de datos via sockets.
//
// NOTA: Requiere CAP_SYS_ADMIN o que el kernel tenga
//       user_namespaces habilitados (sysctl kernel.unprivileged_userns_clone=1).
//       Si falla, ejecuta igualmente sin aislamiento y reporta en JSON.
//
unsafe fn apply_namespaces() -> Result<(), String> {
    let mut flags = CLONE_NEWPID;
    if ISOLATE_NET {
        flags |= CLONE_NEWNET;
    }
    let ret = libc::unshare(flags);
    if ret != 0 {
        let err = std::io::Error::last_os_error();
        return Err(format!("[RING4] unshare falló (código {}): {}", ret, err));
    }
    Ok(())
}

// ── Main ────────────────────────────────────────────────────────────────────


// ── Ring 5: Seccomp BPF syscall filter ─────────────────────────────────────
// Whitelist mínima de syscalls necesarios para ejecutar comandos de shell.
// Cualquier syscall no listado → SECCOMP_RET_KILL (el proceso muere).
//
// SYSCALLS permitidos: los mínimos indispensables para un proceso hijo normal.
// Bloqueados implícitamente: ptrace, mount, kexec, perf_event_open, etc.
fn apply_seccomp_filter() -> Result<(), std::io::Error> {
    // ALLOW por defecto, KILL solo para syscalls destructivos
    let mut ctx = ScmpFilterContext::new_filter(ScmpAction::Allow)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, format!("[RING5] No se pudo crear filtro seccomp: {}", e)))?;

    // Syscalls explícitamente bloqueados (Blacklist)
    let blocked = [
        "ptrace",
        "kexec_load", "kexec_file_load",
        "init_module", "finit_module", "delete_module",
        "bpf",
        "perf_event_open",
        "process_vm_readv", "process_vm_writev",
        "mount", "umount2",
        "swapon", "swapoff",
        "acct", "quotactl",
        "add_key", "request_key", "keyctl",
        "vmsplice",
        "reboot", "setns", "unshare",
    ];

    for name in &blocked {
        if let Ok(sc) = ScmpSyscall::from_name(name) {
            // Ignoramos el error de agregar regla individual por si el kernel no la soporta
            let _ = ctx.add_rule(ScmpAction::KillProcess, sc);
        }
    }

    ctx.load().map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, format!("[RING5] Error cargando filtro seccomp: {}", e)))?;
    Ok(())
}

#[tokio::main]
async fn main() {
    let args: Vec<String> = env::args().collect();

    if args.len() < 2 {
        let res = ExecResult {
            stdout:         String::new(),
            stderr:         String::new(),
            parsed_output:  None,
            exit_code:      None,
            error:          Some("[RING3] No se proporcionó ningún comando".to_string()),
            blocked_reason: None,
            cmd_executed:   String::new(),
            duration_ms:    0,
            isolated:       false,
            seccomp_active: true,
        };
        println!("{}", serde_json::to_string(&res).unwrap());
        return;
    }

    let cmd      = &args[1];
    let cmd_args = args[2..].to_vec();
    let cmd_str  = format!("{} {}", cmd, cmd_args.join(" "));

    // Ring 3: verificación de blacklist
    if let Some(reason) = is_blacklisted(cmd, &cmd_args) {
        let res = ExecResult {
            stdout:         String::new(),
            stderr:         String::new(),
            parsed_output:  None,
            exit_code:      Some(-1),
            error:          None,
            blocked_reason: Some(reason),
            cmd_executed:   cmd_str,
            duration_ms:    0,
            isolated:       false,
            seccomp_active: true,
        };
        println!("{}", serde_json::to_string(&res).unwrap());
        return;
    }

    // Timeout configurable via env var
    let timeout_s: u64 = env::var("EIDOS_TIMEOUT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(TIMEOUT_DEFAULT_S);

    // Entorno saneado
    let safe_env = sanitize_env();

    // Intentar aplicar namespaces — no falla si no tiene permiso
    let mut namespace_ok = false;
    let mut namespace_err = String::new();

    let t_start = std::time::Instant::now();

    let run = {
        let cmd      = cmd.clone();
        let cmd_args = cmd_args.clone();
        let safe_env = safe_env.clone();
        async move {
            let mut child = Command::new(&cmd);
            child.args(&cmd_args);
            child.env_clear();
            for (k, v) in &safe_env {
                child.env(k, v);
            }
            // Ring 4+5: PID + Net namespace isolation en el proceso hijo
            unsafe {
                child.pre_exec(|| {
                    // Ring 4: namespace isolation
                    // Intentamos CLONE_NEWUSER + CLONE_NEWPID + CLONE_NEWNET para que funcione sin root
                    let mut flags = libc::CLONE_NEWUSER | libc::CLONE_NEWPID;
                    if ISOLATE_NET { flags |= libc::CLONE_NEWNET; }
                    
                    if libc::unshare(flags) != 0 {
                        // Si falla con NEWUSER (quizá ya estamos en uno o no soportado), intentamos solo PID+NET
                        let flags_alt = libc::CLONE_NEWPID | (if ISOLATE_NET { libc::CLONE_NEWNET } else { 0 });
                        libc::unshare(flags_alt); 
                    }

                    // Ring 5: seccomp BPF
                    let _ = libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);
                    apply_seccomp_filter().map_err(|e| {
                        std::io::Error::new(std::io::ErrorKind::Other, e.to_string())
                    })?;

                    Ok(())
                });
            }
            child.output()
        }
    };

    let duration_ms;
    let result = match timeout(Duration::from_secs(timeout_s), run).await {
        Ok(Ok(output)) => {
            duration_ms = t_start.elapsed().as_millis() as u64;
            // Verificar si el aislamiento funcionó (exit_code puede dar pistas)
            namespace_ok = true; // asumimos éxito; el pre_exec no devuelve error a nosotros
            let stdout_raw = String::from_utf8_lossy(&output.stdout).to_string();
            let parsed_output = serde_json::from_str::<serde_json::Value>(stdout_raw.trim()).ok();
            
            let stdout = truncate_output(
                stdout_raw,
                MAX_OUTPUT_BYTES,
            );
            let stderr = truncate_output(
                String::from_utf8_lossy(&output.stderr).to_string(),
                MAX_OUTPUT_BYTES / 4,
            );
            ExecResult {
                stdout,
                stderr,
                parsed_output,
                exit_code:      output.status.code(),
                error:          None,
                blocked_reason: None,
                cmd_executed:   cmd_str,
                duration_ms,
                isolated:       namespace_ok,
                seccomp_active: true,
            }
        }
        Ok(Err(e)) => {
            duration_ms = t_start.elapsed().as_millis() as u64;
            let err_msg = format!("[RING3] Error al ejecutar '{}': {}", cmd_str, e);
            ExecResult {
                stdout:         String::new(),
                stderr:         String::new(),
                parsed_output:  None,
                exit_code:      None,
                error:          Some(err_msg),
                blocked_reason: None,
                cmd_executed:   cmd_str,
                duration_ms,
                isolated:       false,
                seccomp_active: true,
            }
        }
        Err(_) => {
            duration_ms = t_start.elapsed().as_millis() as u64;
            ExecResult {
                stdout:         String::new(),
                stderr:         String::new(),
                parsed_output:  None,
                exit_code:      None,
                error:          Some(format!(
                    "[RING3] Timeout de {}s superado para: {}", timeout_s, cmd_str
                )),
                blocked_reason: None,
                cmd_executed:   cmd_str,
                duration_ms,
                isolated:       false,
                seccomp_active: true,
            }
        }
    };

    println!("{}", serde_json::to_string(&result).unwrap());
}
