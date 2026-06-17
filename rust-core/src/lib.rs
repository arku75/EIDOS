#![allow(non_local_definitions)] // PyO3 0.20 macro limitation with Rust 1.91+

pub mod knowledge;

#[cfg(feature = "python")]
use pyo3::prelude::*;
#[cfg(feature = "python")]
use pyo3::exceptions::PyRuntimeError;

#[cfg(feature = "python")]
use std::collections::HashMap;
#[cfg(feature = "python")]
use std::path::PathBuf;

#[cfg(feature = "python")]
use knowledge::{KnowledgeDB, KnowledgeEntry};

// ═══════════════════════════════════════════════════════════════════════════
// PyKnowledgeDB — wraps KnowledgeDB for Python via rust_bridge.py
// ═══════════════════════════════════════════════════════════════════════════

#[cfg(feature = "python")]
#[derive(Clone, Default, serde::Serialize)]
struct LanguageStats {
    files_observed: usize,
    total_functions: usize,
    total_lines: usize,
}

#[cfg(feature = "python")]
#[derive(Clone, Default, serde::Serialize)]
struct LibraryStats {
    language: String,
    usage_count: usize,
}

#[cfg(feature = "python")]
#[pyclass(name = "KnowledgeDB")]
struct PyKnowledgeDB {
    inner: KnowledgeDB,
    verbose: bool,
    languages: HashMap<String, LanguageStats>,
    libraries: HashMap<String, LibraryStats>,
}

#[cfg(feature = "python")]
#[pymethods]
impl PyKnowledgeDB {
    #[new]
    #[pyo3(signature = (verbose = false))]
    fn new(verbose: bool) -> PyResult<Self> {
        let data_dir = dirs::home_dir()
            .map(|h| h.join(".local/share/eidos"))
            .unwrap_or_else(|| PathBuf::from("./eidos_data"));

        if verbose {
            eprintln!("[eidos_core] Initializing KnowledgeDB at {:?}", data_dir);
        }

        Ok(Self {
            inner: KnowledgeDB::new(data_dir),
            verbose,
            languages: HashMap::new(),
            libraries: HashMap::new(),
        })
    }

    /// Analyze a source file and extract knowledge (language, imports, functions).
    fn observe_file(&mut self, file_path: &str) -> PyResult<String> {
        let path = std::path::Path::new(file_path);
        if !path.exists() {
            return Err(PyRuntimeError::new_err(format!("File not found: {}", file_path)));
        }

        let content = std::fs::read_to_string(path)
            .map_err(|e| PyRuntimeError::new_err(format!("Read error: {}", e)))?;

        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
        let language = detect_language(ext);
        let lines = content.lines().count();
        let functions = count_functions(&content, &language);
        let libs = extract_libraries(&content, &language);
        let patterns = extract_patterns(&content, &language);

        let lang_stats = self.languages.entry(language.clone()).or_default();
        lang_stats.files_observed += 1;
        lang_stats.total_functions += functions;
        lang_stats.total_lines += lines;

        for lib in &libs {
            let lib_stats = self.libraries.entry(lib.clone()).or_default();
            lib_stats.language = language.clone();
            lib_stats.usage_count += 1;
        }

        let entry_id = format!("file:{}", file_path);
        let entry = KnowledgeEntry {
            id: entry_id,
            content: format!("{} ({} lines, {} functions)", file_path, lines, functions),
            category: format!("code:{}", language),
            created_at: chrono::Utc::now(),
            updated_at: chrono::Utc::now(),
            metadata: serde_json::json!({
                "language": language, "lines": lines,
                "functions": functions, "libraries": libs, "patterns": patterns,
            }),
            embedding: None,
        };
        let _ = self.inner.insert(entry);

        let result = serde_json::json!({
            "file": file_path, "language": language, "lines": lines,
            "functions": functions, "libraries": libs, "patterns": patterns,
        });

        if self.verbose {
            eprintln!("[eidos_core] Observed: {} ({}, {} funcs, {} libs)",
                file_path, language, functions, libs.len());
        }

        serde_json::to_string(&result)
            .map_err(|e| PyRuntimeError::new_err(format!("JSON error: {}", e)))
    }

    fn get_stats(&self) -> PyResult<String> {
        let result = serde_json::json!({
            "total_entries": self.inner.count(),
            "total_languages": self.languages.len(),
            "total_libraries": self.libraries.len(),
            "languages": self.languages,
            "libraries": self.libraries,
        });
        serde_json::to_string(&result)
            .map_err(|e| PyRuntimeError::new_err(format!("JSON error: {}", e)))
    }

    fn export_for_sync(&self) -> PyResult<String> {
        let result = serde_json::json!({
            "version": "0.2.0",
            "languages": self.languages,
            "libraries": self.libraries,
            "entries_count": self.inner.count(),
        });
        serde_json::to_string(&result)
            .map_err(|e| PyRuntimeError::new_err(format!("JSON error: {}", e)))
    }

    fn import_from_sync(&mut self, json_str: &str) -> PyResult<()> {
        let data: serde_json::Value = serde_json::from_str(json_str)
            .map_err(|e| PyRuntimeError::new_err(format!("JSON parse error: {}", e)))?;

        if let Some(langs) = data.get("languages").and_then(|v| v.as_object()) {
            for (name, stats) in langs {
                let entry = self.languages.entry(name.clone()).or_default();
                if let Some(count) = stats.get("files_observed").and_then(|v| v.as_u64()) {
                    entry.files_observed += count as usize;
                }
            }
        }
        if let Some(libs) = data.get("libraries").and_then(|v| v.as_object()) {
            for (name, stats) in libs {
                let entry = self.libraries.entry(name.clone()).or_default();
                if let Some(count) = stats.get("usage_count").and_then(|v| v.as_u64()) {
                    entry.usage_count += count as usize;
                }
                if let Some(lang) = stats.get("language").and_then(|v| v.as_str()) {
                    entry.language = lang.to_string();
                }
            }
        }
        Ok(())
    }

    #[pyo3(signature = (id, content, category, metadata = None))]
    fn insert(&self, id: String, content: String, category: String,
              metadata: Option<String>) -> PyResult<String> {
        let meta = match metadata {
            Some(json_str) => serde_json::from_str(&json_str).unwrap_or(serde_json::json!({})),
            None => serde_json::json!({}),
        };
        let entry = KnowledgeEntry {
            id: id.clone(), content, category,
            created_at: chrono::Utc::now(), updated_at: chrono::Utc::now(),
            metadata: meta, embedding: None,
        };
        self.inner.insert(entry).map_err(|e| PyRuntimeError::new_err(e))?;
        Ok(id)
    }

    fn get(&self, id: &str) -> Option<String> {
        self.inner.get(id).map(|entry| serde_json::to_string(&entry).unwrap_or_default())
    }

    #[pyo3(signature = (query, limit = 10))]
    fn search(&self, query: &str, limit: usize) -> PyResult<String> {
        let results = self.inner.search(query, limit);
        let out: Vec<serde_json::Value> = results.iter().map(|r| {
            serde_json::json!({
                "id": r.entry.id, "content": r.entry.content,
                "category": r.entry.category, "score": r.score,
            })
        }).collect();
        serde_json::to_string(&out)
            .map_err(|e| PyRuntimeError::new_err(format!("JSON error: {}", e)))
    }

    fn delete(&self, id: &str) -> PyResult<()> {
        self.inner.delete(id).map_err(|e| PyRuntimeError::new_err(e))
    }

    fn count(&self) -> usize { self.inner.count() }

    fn categories(&self) -> Vec<String> { self.inner.list_categories() }
}

// ═══════════════════════════════════════════════════════════════════════════
// FileObserver stub
// ═══════════════════════════════════════════════════════════════════════════

#[cfg(feature = "python")]
#[pyclass(name = "FileObserver")]
struct PyFileObserver {
    watched_paths: Vec<String>,
    extensions: Vec<String>,
    running: bool,
}

#[cfg(feature = "python")]
#[pymethods]
impl PyFileObserver {
    #[new]
    fn new() -> Self {
        Self {
            watched_paths: Vec::new(),
            extensions: vec![".py".into(), ".rs".into(), ".go".into(),
                             ".js".into(), ".ts".into(), ".cpp".into()],
            running: false,
        }
    }

    fn watch(&mut self, path: &str) {
        self.watched_paths.push(path.to_string());
        self.running = true;
    }

    fn stop(&mut self) { self.running = false; }

    fn add_extension(&mut self, ext: &str) {
        self.extensions.push(ext.to_string());
    }

    fn get_event(&self) -> Option<HashMap<String, Vec<String>>> { None }
}

// ═══════════════════════════════════════════════════════════════════════════
// Module definition
// ═══════════════════════════════════════════════════════════════════════════

#[cfg(feature = "python")]
#[pymodule]
fn eidos_core(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<PyKnowledgeDB>()?;
    m.add_class::<PyFileObserver>()?;
    m.add("__version__", "0.2.0")?;
    m.add("RUST_AVAILABLE", true)?;
    Ok(())
}

// ═══════════════════════════════════════════════════════════════════════════
// Source file analysis utilities
// ═══════════════════════════════════════════════════════════════════════════

#[cfg(feature = "python")]
fn detect_language(ext: &str) -> String {
    match ext {
        "py" => "python", "rs" => "rust", "go" => "go",
        "js" | "mjs" => "javascript", "ts" | "tsx" => "typescript",
        "cpp" | "cc" | "cxx" => "cpp", "c" => "c",
        "java" => "java", "rb" => "ruby", "php" => "php",
        "swift" => "swift", "kt" | "kts" => "kotlin", "lua" => "lua",
        "sh" | "bash" | "zsh" => "shell",
        "toml" => "toml", "yaml" | "yml" => "yaml", "json" => "json",
        "html" | "htm" => "html", "css" | "scss" => "css",
        _ => "unknown",
    }.to_string()
}

#[cfg(feature = "python")]
fn count_functions(content: &str, language: &str) -> usize {
    let mut count = 0;
    for line in content.lines() {
        let t = line.trim();
        match language {
            "python" => {
                if t.starts_with("def ") || t.starts_with("async def ") { count += 1; }
            }
            "rust" => {
                if (t.starts_with("fn ") || t.starts_with("pub fn ")
                    || t.starts_with("pub(crate) fn ")
                    || t.starts_with("async fn ") || t.starts_with("pub async fn "))
                    && !t.starts_with("//")
                { count += 1; }
            }
            "go" => { if t.starts_with("func ") { count += 1; } }
            "javascript" | "typescript" => {
                if t.starts_with("function ") || t.starts_with("async function ")
                    || t.contains("=> {")
                { count += 1; }
            }
            "cpp" | "c" | "java" => {
                if t.contains('(') && t.contains(')')
                    && (t.ends_with('{') || t.ends_with(") {"))
                    && !t.starts_with("if ") && !t.starts_with("for ")
                    && !t.starts_with("while ") && !t.starts_with("//")
                { count += 1; }
            }
            _ => {}
        }
    }
    count
}

#[cfg(feature = "python")]
fn extract_libraries(content: &str, language: &str) -> Vec<String> {
    let mut libs = Vec::new();
    for line in content.lines() {
        let t = line.trim();
        match language {
            "python" => {
                if let Some(rest) = t.strip_prefix("import ") {
                    let m = rest.split_whitespace().next().unwrap_or("")
                        .split('.').next().unwrap_or("");
                    if !m.is_empty() { libs.push(m.to_string()); }
                } else if let Some(rest) = t.strip_prefix("from ") {
                    let m = rest.split_whitespace().next().unwrap_or("")
                        .split('.').next().unwrap_or("").trim_start_matches('.');
                    if !m.is_empty() { libs.push(m.to_string()); }
                }
            }
            "rust" => {
                if let Some(rest) = t.strip_prefix("use ") {
                    let c = rest.split("::").next().unwrap_or("").trim_end_matches(';');
                    if !c.is_empty() && !["std","self","super","crate"].contains(&c) {
                        libs.push(c.to_string());
                    }
                }
            }
            "go" => {
                if t.starts_with('"') && t.ends_with('"') {
                    let pkg = t.trim_matches('"').rsplit('/').next().unwrap_or("");
                    if !pkg.is_empty() { libs.push(pkg.to_string()); }
                }
            }
            "javascript" | "typescript" => {
                if t.contains("require(") || t.contains("from '") || t.contains("from \"") {
                    for delim in ["'", "\""] {
                        if let Some(start) = t.find(&format!("from {}", delim)) {
                            let rest = &t[start + 6..];
                            if let Some(end) = rest.find(delim) {
                                let name = rest[..end].split('/').next().unwrap_or("");
                                if !name.starts_with('.') { libs.push(name.to_string()); }
                            }
                        }
                    }
                }
            }
            "cpp" | "c" => {
                if let Some(rest) = t.strip_prefix("#include ") {
                    let h = rest.trim_start_matches(|c: char| c == '<' || c == '"')
                        .split(|c: char| c == '>' || c == '"').next().unwrap_or("")
                        .split('/').next().unwrap_or("");
                    if !h.is_empty() { libs.push(h.to_string()); }
                }
            }
            _ => {}
        }
    }
    libs.sort();
    libs.dedup();
    libs
}

#[cfg(feature = "python")]
fn extract_patterns(content: &str, language: &str) -> Vec<String> {
    let mut patterns = Vec::new();
    let checks: &[(&str, &str)] = match language {
        "python" => &[
            ("async def ", "async_await"), ("@", "decorators"), ("class ", "oop"),
            ("with ", "context_manager"), ("yield", "generators"), ("lambda", "lambda"),
            ("try:", "error_handling"), ("typing", "type_hints"),
        ],
        "rust" => &[
            ("async fn", "async_await"), ("#[derive", "derive_macros"),
            ("impl ", "impl_blocks"), ("trait ", "traits"),
            ("Result<", "result_type"), ("Option<", "option_type"),
            ("unsafe ", "unsafe_code"), ("#[test]", "tests"),
        ],
        "go" => &[
            ("go func", "goroutines"), ("chan ", "channels"),
            ("interface {", "interfaces"), ("defer ", "defer"), ("select {", "select"),
        ],
        _ => &[],
    };
    for (marker, pattern) in checks {
        if content.contains(marker) { patterns.push(pattern.to_string()); }
    }
    patterns
}
