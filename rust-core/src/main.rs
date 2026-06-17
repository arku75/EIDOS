use eidos_core::knowledge::{KnowledgeDB, KnowledgeEntry};
use std::env;
use std::path::PathBuf;
use chrono::Utc;
use serde_json::json;

fn main() {
    env_logger::init();
    
    let args: Vec<String> = env::args().collect();
    
    // Default data directory
    let data_dir = env::var("EIDOS_DATA_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            dirs::home_dir()
                .map(|h| h.join(".local/share/eidos"))
                .unwrap_or_else(|| PathBuf::from("./eidos_data"))
        });
    
    let db = KnowledgeDB::new(data_dir);
    
    if args.len() < 2 {
        print_usage();
        return;
    }
    
    match args[1].as_str() {
        "insert" => cmd_insert(&db, &args),
        "get" => cmd_get(&db, &args),
        "search" => cmd_search(&db, &args),
        "delete" => cmd_delete(&db, &args),
        "list" => cmd_list(&db),
        "count" => cmd_count(&db),
        "clear" => cmd_clear(&db),
        "categories" => cmd_categories(&db),
        _ => print_usage(),
    }
}

fn print_usage() {
    println!("EIDOS Knowledge DB (Rust)");
    println!("Uso: eidos_knowledge <comando> [args...]");
    println!();
    println!("Comandos:");
    println!("  insert <id> <categoria> <contenido>   Inserta entrada");
    println!("  get <id>                              Obtiene entrada por ID");
    println!("  search <query> [limit]                Busca entradas");
    println!("  delete <id>                           Elimina entrada");
    println!("  list                                  Lista todas las entradas");
    println!("  count                                 Muestra conteo total");
    println!("  categories                            Lista categorías");
    println!("  clear                                 Elimina TODO (cuidado!)");
}

fn cmd_insert(db: &KnowledgeDB, args: &[String]) {
    if args.len() < 5 {
        eprintln!("Uso: insert <id> <categoria> <contenido>");
        std::process::exit(1);
    }
    
    let id = args[2].clone();
    let category = args[3].clone();
    let content = args[4..].join(" ");
    
    let entry = KnowledgeEntry {
        id: id.clone(),
        content,
        category,
        created_at: Utc::now(),
        updated_at: Utc::now(),
        metadata: json!({}),
        embedding: None,
    };
    
    match db.insert(entry) {
        Ok(_) => println!("✓ Insertado: {}", id),
        Err(e) => {
            eprintln!("✗ Error: {}", e);
            std::process::exit(1);
        }
    }
}

fn cmd_get(db: &KnowledgeDB, args: &[String]) {
    if args.len() < 3 {
        eprintln!("Uso: get <id>");
        std::process::exit(1);
    }
    
    let id = &args[2];
    
    match db.get(id) {
        Some(entry) => {
            println!("ID: {}", entry.id);
            println!("Categoría: {}", entry.category);
            println!("Contenido: {}", entry.content);
            println!("Creado: {}", entry.created_at);
        }
        None => {
            eprintln!("✗ No encontrado: {}", id);
            std::process::exit(1);
        }
    }
}

fn cmd_search(db: &KnowledgeDB, args: &[String]) {
    if args.len() < 3 {
        eprintln!("Uso: search <query> [limit]");
        std::process::exit(1);
    }
    
    let query = &args[2];
    let limit = args.get(3)
        .and_then(|s| s.parse().ok())
        .unwrap_or(10);
    
    let results = db.search(query, limit);
    
    if results.is_empty() {
        println!("Sin resultados para: {}", query);
        return;
    }
    
    println!("Resultados ({}):", results.len());
    for result in results {
        println!("  [{}] {} (score: {:.2})",
            result.entry.category,
            result.entry.id,
            result.score
        );
        println!("    {}", result.entry.content.chars().take(100).collect::<String>());
    }
}

fn cmd_delete(db: &KnowledgeDB, args: &[String]) {
    if args.len() < 3 {
        eprintln!("Uso: delete <id>");
        std::process::exit(1);
    }
    
    let id = &args[2];
    
    match db.delete(id) {
        Ok(_) => println!("✓ Eliminado: {}", id),
        Err(e) => {
            eprintln!("✗ Error: {}", e);
            std::process::exit(1);
        }
    }
}

fn cmd_list(db: &KnowledgeDB) {
    let categories = db.list_categories();
    let count = db.count();
    
    println!("Total de entradas: {}", count);
    println!("Categorías: {}", categories.len());
    for cat in categories {
        // Count entries in category
        // Note: In real implementation, we'd add a method for this
        println!("  - {}", cat);
    }
}

fn cmd_count(db: &KnowledgeDB) {
    println!("{}", db.count());
}

fn cmd_categories(db: &KnowledgeDB) {
    let categories = db.list_categories();
    for cat in categories {
        println!("{}", cat);
    }
}

fn cmd_clear(db: &KnowledgeDB) {
    print!("¿Eliminar TODAS las entradas? [s/N]: ");
    std::io::Write::flush(&mut std::io::stdout()).unwrap();
    
    let mut input = String::new();
    std::io::stdin().read_line(&mut input).unwrap();
    
    if input.trim().to_lowercase() == "s" {
        match db.clear() {
            Ok(_) => println!("✓ Base de datos limpiada"),
            Err(e) => {
                eprintln!("✗ Error: {}", e);
                std::process::exit(1);
            }
        }
    } else {
        println!("Cancelado");
    }
}
