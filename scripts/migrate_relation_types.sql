-- ============================================================================
-- EIDOS relation_types migration
-- Añade tabla relation_types a evolution_brain.db y siembra tipos semánticos
-- fundamentales: IS_A, PART_OF, CAUSES, USES, etc.
--
-- Esto permite razonamiento real: transitivo, simétrico, detección de
-- contradicciones — en lugar de solo recuperar keywords.
-- ============================================================================

-- 1. Crear tabla relation_types
CREATE TABLE IF NOT EXISTS relation_types (
    type TEXT PRIMARY KEY,
    category TEXT NOT NULL DEFAULT 'functional',
    description TEXT DEFAULT '',
    inverse TEXT,
    transitive INTEGER DEFAULT 0,
    symmetric INTEGER DEFAULT 0
);

-- 2. Índices
CREATE INDEX IF NOT EXISTS idx_relation_types_category ON relation_types(category);
CREATE INDEX IF NOT EXISTS idx_relation_types_inverse ON relation_types(inverse);

-- 3. Sembrar tipos semánticos fundamentales
--    Usamos INSERT OR IGNORE para que sea idempotente

-- ── Hierarchy ──────────────────────────────────────────────────────────────
INSERT OR IGNORE INTO relation_types (type, category, description, inverse, transitive, symmetric)
VALUES ('IS_A', 'hierarchy', 'Subtype/subclass relation (hypernym). A IS_A B means A is a type of B.',
        'HAS_INSTANCE', 1, 0);

INSERT OR IGNORE INTO relation_types (type, category, description, inverse, transitive, symmetric)
VALUES ('HAS_INSTANCE', 'hierarchy', 'Inverse of IS_A. B HAS_INSTANCE A means B has A as an instance.',
        'IS_A', 0, 0);

INSERT OR IGNORE INTO relation_types (type, category, description, inverse, transitive, symmetric)
VALUES ('PART_OF', 'hierarchy', 'Mereological part-whole. A PART_OF B means A is a component of B.',
        'HAS_PART', 1, 0);

INSERT OR IGNORE INTO relation_types (type, category, description, inverse, transitive, symmetric)
VALUES ('HAS_PART', 'hierarchy', 'Inverse of PART_OF. B HAS_PART A means B contains A as a part.',
        'PART_OF', 0, 0);

-- ── Causal ─────────────────────────────────────────────────────────────────
INSERT OR IGNORE INTO relation_types (type, category, description, inverse, transitive, symmetric)
VALUES ('CAUSES', 'causal', 'A CAUSES B means A brings about, produces, or leads to B.',
        'CAUSED_BY', 0, 0);

INSERT OR IGNORE INTO relation_types (type, category, description, inverse, transitive, symmetric)
VALUES ('CAUSED_BY', 'causal', 'Inverse of CAUSES. B CAUSED_BY A means B is brought about by A.',
        'CAUSES', 0, 0);

-- ── Functional ─────────────────────────────────────────────────────────────
INSERT OR IGNORE INTO relation_types (type, category, description, inverse, transitive, symmetric)
VALUES ('USES', 'functional', 'A USES B means A utilizes B as a tool, resource, or dependency.',
        'USED_BY', 0, 0);

INSERT OR IGNORE INTO relation_types (type, category, description, inverse, transitive, symmetric)
VALUES ('USED_BY', 'functional', 'Inverse of USES. B USED_BY A means B is utilized by A.',
        'USES', 0, 0);

INSERT OR IGNORE INTO relation_types (type, category, description, inverse, transitive, symmetric)
VALUES ('DEPENDS_ON', 'functional', 'A DEPENDS_ON B means A requires B to function.',
        'SUPPORTS', 0, 0);

INSERT OR IGNORE INTO relation_types (type, category, description, inverse, transitive, symmetric)
VALUES ('SUPPORTS', 'functional', 'Inverse of DEPENDS_ON. B SUPPORTS A means B is required by A.',
        'DEPENDS_ON', 0, 0);

-- ── Similarity ─────────────────────────────────────────────────────────────
INSERT OR IGNORE INTO relation_types (type, category, description, inverse, transitive, symmetric)
VALUES ('SIMILAR_TO', 'similarity', 'A SIMILAR_TO B means A and B share properties or function.',
        'SIMILAR_TO', 0, 1);

-- ── Temporal ───────────────────────────────────────────────────────────────
INSERT OR IGNORE INTO relation_types (type, category, description, inverse, transitive, symmetric)
VALUES ('BEFORE', 'temporal', 'A BEFORE B means A precedes B in time.',
        'AFTER', 1, 0);

INSERT OR IGNORE INTO relation_types (type, category, description, inverse, transitive, symmetric)
VALUES ('AFTER', 'temporal', 'Inverse of BEFORE. B AFTER A means B follows A in time.',
        'BEFORE', 1, 0);

-- ── Spatial ────────────────────────────────────────────────────────────────
INSERT OR IGNORE INTO relation_types (type, category, description, inverse, transitive, symmetric)
VALUES ('LOCATED_IN', 'spatial', 'A LOCATED_IN B means A is spatially within B.',
        'CONTAINS', 0, 0);

INSERT OR IGNORE INTO relation_types (type, category, description, inverse, transitive, symmetric)
VALUES ('CONTAINS', 'spatial', 'Inverse of LOCATED_IN. B CONTAINS A means B spatially encompasses A.',
        'LOCATED_IN', 0, 0);

-- 4. Add a semantic_relation_type column to knowledge_edges (optional, for
--    edges that get classified into canonical types). This is NULLABLE — only
--    set when we can confidently map the existing relation_type to a canonical
--    semantic type.
ALTER TABLE knowledge_edges ADD COLUMN semantic_type TEXT DEFAULT NULL;
CREATE INDEX IF NOT EXISTS idx_edges_semantic_type ON knowledge_edges(semantic_type);

-- 5. Verify
SELECT 'relation_types created', COUNT(*) FROM relation_types;
SELECT 'knowledge_edges with semantic_type column', COUNT(*) FROM knowledge_edges WHERE semantic_type IS NOT NULL;
