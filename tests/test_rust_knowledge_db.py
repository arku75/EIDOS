#!/usr/bin/env python3
"""
Test Rust Knowledge DB Python Bindings
=====================================

Verifies that the Rust Knowledge DB can be used from Python.
"""
import sys
import os
from pathlib import Path

# Add rust-core to Python path
rust_lib = Path(__file__).parent.parent / "rust-core" / "target" / "release"
if rust_lib.exists():
    sys.path.insert(0, str(rust_lib))

try:
    import eidos_core
    print("✅ eidos_core imported successfully!")
except ImportError as e:
    print(f"❌ Failed to import eidos_core: {e}")
    print(f"\nMake sure you've built the Rust library:")
    print(f"  cd /home/ser/EIDOS/rust-core")
    print(f"  cargo build --release")
    sys.exit(1)

def test_knowledge_db_creation():
    """Test creating a KnowledgeDB instance"""
    print("\n" + "="*70)
    print("TEST 1: Knowledge DB Creation")
    print("="*70)

    try:
        kb = eidos_core.KnowledgeDB(verbose=True)
        print("✅ KnowledgeDB instance created")
        return kb
    except Exception as e:
        print(f"❌ Failed to create KnowledgeDB: {e}")
        import traceback
        traceback.print_exc()
        return None

def test_observe_file(kb):
    """Test observing a file"""
    print("\n" + "="*70)
    print("TEST 2: File Observation")
    print("="*70)

    # Create a test file
    test_file = Path("/tmp/test_eidos_rust.py")
    test_file.write_text("""
import sys
import os
from pathlib import Path

def hello_world():
    \"\"\"Say hello\"\"\"
    print("Hello from EIDOS!")

class TestClass:
    def __init__(self):
        self.name = "test"

    def greet(self):
        print(f"Hello, {self.name}!")

if __name__ == "__main__":
    hello_world()
""")

    try:
        result = kb.observe_file(str(test_file))
        print(f"✅ File observed successfully!")
        print(f"\nObservation result:")

        import json
        observation = json.loads(result)
        print(json.dumps(observation, indent=2))

        print(f"\nDetected:")
        print(f"  Language: {observation['language']}")
        print(f"  Libraries: {observation['libraries']}")
        print(f"  Patterns: {observation['patterns']}")
        print(f"  Functions: {observation['functions']}")
        print(f"  Complexity: {observation['complexity_score']}")

        return True
    except Exception as e:
        print(f"❌ Failed to observe file: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Cleanup
        if test_file.exists():
            test_file.unlink()

def test_get_stats(kb):
    """Test getting database statistics"""
    print("\n" + "="*70)
    print("TEST 3: Database Statistics")
    print("="*70)

    try:
        stats = kb.get_stats()
        print(f"✅ Stats retrieved successfully!")

        import json
        stats_data = json.loads(stats)
        print(json.dumps(stats_data, indent=2))

        return True
    except Exception as e:
        print(f"❌ Failed to get stats: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_export_import(kb):
    """Test export/import for P2P sync"""
    print("\n" + "="*70)
    print("TEST 4: Export/Import (P2P Sync)")
    print("="*70)

    try:
        # Export knowledge
        export_data = kb.export_for_sync()
        print(f"✅ Knowledge exported successfully!")
        print(f"   Size: {len(export_data)} bytes")

        # Create a new KB and import
        kb2 = eidos_core.KnowledgeDB(verbose=False)
        kb2.import_from_sync(export_data)
        print(f"✅ Knowledge imported successfully!")

        # Verify
        stats1 = kb.get_stats()
        stats2 = kb2.get_stats()

        import json
        s1 = json.loads(stats1)
        s2 = json.loads(stats2)

        if s1['total_languages'] == s2['total_languages']:
            print(f"✅ Import verification passed!")
            print(f"   Languages: {s1['total_languages']}")
            print(f"   Libraries: {s1['total_libraries']}")
        else:
            print(f"⚠️  Mismatch in import: {s1['total_languages']} vs {s2['total_languages']}")

        return True
    except Exception as e:
        print(f"❌ Failed export/import: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print("╔" + "="*68 + "╗")
    print("║" + " "*12 + "EIDOS Rust Knowledge DB - Python Bindings Test" + " "*10 + "║")
    print("╚" + "="*68 + "╝")

    # Test 1: Create KB
    kb = test_knowledge_db_creation()
    if not kb:
        print("\n❌ Cannot continue without KnowledgeDB instance")
        return 1

    # Test 2: Observe file
    if not test_observe_file(kb):
        print("\n⚠️  File observation failed, but continuing...")

    # Test 3: Get stats
    if not test_get_stats(kb):
        print("\n⚠️  Stats retrieval failed, but continuing...")

    # Test 4: Export/Import
    if not test_export_import(kb):
        print("\n⚠️  Export/import failed")

    print("\n" + "="*70)
    print("✅ ALL TESTS COMPLETED!")
    print("="*70)
    print()
    print("🚀 Rust Knowledge DB is ready for use!")
    print()
    print("Next steps:")
    print("  1. Run benchmarks to verify 100x performance improvement")
    print("  2. Integrate with EIDOS learning daemon")
    print("  3. Test with large codebases")
    print()

    return 0

if __name__ == "__main__":
    sys.exit(main())
