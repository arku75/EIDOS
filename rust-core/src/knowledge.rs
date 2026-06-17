use dashmap::DashMap;
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::path::PathBuf;
use parking_lot::RwLock;
use chrono::{DateTime, Utc};
use log::{info, warn};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KnowledgeEntry {
    pub id: String,
    pub content: String,
    pub category: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub metadata: serde_json::Value,
    pub embedding: Option<Vec<f32>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchResult {
    pub entry: KnowledgeEntry,
    pub score: f32,
}

pub struct KnowledgeDB {
    storage: Arc<DashMap<String, KnowledgeEntry>>,
    data_dir: PathBuf,
    autosave: RwLock<bool>,
}

impl KnowledgeDB {
    pub fn new(data_dir: PathBuf) -> Self {
        info!("Initializing KnowledgeDB at {:?}", data_dir);
        let storage = Arc::new(DashMap::new());
        
        let db = Self {
            storage,
            data_dir,
            autosave: RwLock::new(true),
        };
        
        // Load existing data if present
        if let Err(e) = db.load() {
            warn!("Could not load existing data: {}", e);
        }
        
        db
    }
    
    pub fn insert(&self, entry: KnowledgeEntry) -> Result<(), String> {
        let id = entry.id.clone();
        self.storage.insert(id.clone(), entry);
        
        if *self.autosave.read() {
            self.save()?;
        }
        
        info!("Inserted entry: {}", id);
        Ok(())
    }
    
    pub fn get(&self, id: &str) -> Option<KnowledgeEntry> {
        self.storage.get(id).map(|entry| entry.clone())
    }
    
    pub fn search(&self, query: &str, limit: usize) -> Vec<SearchResult> {
        let query_lower = query.to_lowercase();
        
        let results: Vec<SearchResult> = self.storage
            .iter()
            .filter_map(|entry| {
                let content_lower = entry.content.to_lowercase();
                let category_lower = entry.category.to_lowercase();
                
                if content_lower.contains(&query_lower) || 
                   category_lower.contains(&query_lower) {
                    // Simple scoring based on match position
                    let score = if content_lower.starts_with(&query_lower) {
                        1.0
                    } else if content_lower.contains(&query_lower) {
                        0.8
                    } else {
                        0.5
                    };
                    
                    Some(SearchResult {
                        entry: entry.clone(),
                        score,
                    })
                } else {
                    None
                }
            })
            .collect();
        
        // Sort by score and limit
        let mut results = results;
        results.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap());
        results.truncate(limit);
        
        results
    }
    
    pub fn delete(&self, id: &str) -> Result<(), String> {
        self.storage.remove(id);
        
        if *self.autosave.read() {
            self.save()?;
        }
        
        info!("Deleted entry: {}", id);
        Ok(())
    }
    
    pub fn list_categories(&self) -> Vec<String> {
        let mut categories: std::collections::HashSet<String> = self.storage
            .iter()
            .map(|entry| entry.category.clone())
            .collect();
        
        let mut result: Vec<String> = categories.drain().collect();
        result.sort();
        result
    }
    
    pub fn count(&self) -> usize {
        self.storage.len()
    }
    
    pub fn save(&self) -> Result<(), String> {
        std::fs::create_dir_all(&self.data_dir)
            .map_err(|e| format!("Failed to create data dir: {}", e))?;
        
        let data: Vec<KnowledgeEntry> = self.storage
            .iter()
            .map(|entry| entry.clone())
            .collect();
        
        let json = serde_json::to_string_pretty(&data)
            .map_err(|e| format!("Serialization error: {}", e))?;
        
        let file_path = self.data_dir.join("knowledge_db.json");
        std::fs::write(&file_path, json)
            .map_err(|e| format!("Write error: {}", e))?;
        
        info!("Saved {} entries to {:?}", data.len(), file_path);
        Ok(())
    }
    
    pub fn load(&self) -> Result<(), String> {
        let file_path = self.data_dir.join("knowledge_db.json");
        
        if !file_path.exists() {
            info!("No existing database found at {:?}", file_path);
            return Ok(());
        }
        
        let json = std::fs::read_to_string(&file_path)
            .map_err(|e| format!("Read error: {}", e))?;
        
        let data: Vec<KnowledgeEntry> = serde_json::from_str(&json)
            .map_err(|e| format!("Deserialization error: {}", e))?;
        
        for entry in data {
            self.storage.insert(entry.id.clone(), entry);
        }
        
        info!("Loaded {} entries from {:?}", self.storage.len(), file_path);
        Ok(())
    }
    
    pub fn clear(&self) -> Result<(), String> {
        self.storage.clear();
        
        if *self.autosave.read() {
            self.save()?;
        }
        
        info!("Cleared all entries");
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;
    
    fn create_test_db() -> (KnowledgeDB, TempDir) {
        let temp_dir = TempDir::new().unwrap();
        let db = KnowledgeDB::new(temp_dir.path().to_path_buf());
        (db, temp_dir)
    }
    
    fn create_test_entry(id: &str, content: &str, category: &str) -> KnowledgeEntry {
        KnowledgeEntry {
            id: id.to_string(),
            content: content.to_string(),
            category: category.to_string(),
            created_at: Utc::now(),
            updated_at: Utc::now(),
            metadata: serde_json::json!({}),
            embedding: None,
        }
    }
    
    #[test]
    fn test_insert_and_get() {
        let (db, _temp) = create_test_db();
        let entry = create_test_entry("test1", "Test content", "test");
        
        db.insert(entry.clone()).unwrap();
        let retrieved = db.get("test1").unwrap();
        
        assert_eq!(retrieved.id, "test1");
        assert_eq!(retrieved.content, "Test content");
    }
    
    #[test]
    fn test_search() {
        let (db, _temp) = create_test_db();
        
        db.insert(create_test_entry("1", "Rust programming", "code")).unwrap();
        db.insert(create_test_entry("2", "Python scripting", "code")).unwrap();
        db.insert(create_test_entry("3", "Machine learning", "ai")).unwrap();
        
        let results = db.search("rust", 10);
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].entry.id, "1");
    }
    
    #[test]
    fn test_delete() {
        let (db, _temp) = create_test_db();
        let entry = create_test_entry("del1", "To delete", "test");
        
        db.insert(entry).unwrap();
        assert!(db.get("del1").is_some());
        
        db.delete("del1").unwrap();
        assert!(db.get("del1").is_none());
    }
}
