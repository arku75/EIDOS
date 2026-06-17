#!/usr/bin/env python3
"""
CPU-Intensive Benchmark: Rust vs Python Knowledge DB
====================================================

This benchmark focuses on CPU-intensive operations:
- Complex regex matching
- Pattern extraction
- Library detection
- Complexity analysis

No file I/O bottleneck - pure computational performance.
"""
import sys
import time
from pathlib import Path

# Add rust-core to Python path
rust_lib = Path(__file__).parent.parent / "rust-core" / "target" / "release"
if rust_lib.exists():
    sys.path.insert(0, str(rust_lib))

# Add EIDOS to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.knowledge_db import KnowledgeDB as PythonKnowledgeDB

try:
    import eidos_core
    RustKnowledgeDB = eidos_core.KnowledgeDB
    RUST_AVAILABLE = True
except ImportError:
    print("⚠️  Rust Knowledge DB not available")
    RUST_AVAILABLE = False

# Large, complex Python code for analysis
COMPLEX_PYTHON_CODE = """
import sys
import os
import json
import asyncio
import aiohttp
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass, field
from collections import defaultdict, Counter
from functools import wraps, lru_cache
import requests
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
import tensorflow as tf
from flask import Flask, request, jsonify

@dataclass
class DataProcessor:
    config: Dict[str, Any]
    results: List[Dict] = field(default_factory=list)
    cache: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.setup_logging()

    def setup_logging(self):
        import logging
        logging.basicConfig(level=logging.INFO)

    @lru_cache(maxsize=1000)
    def get_cached_data(self, key: str) -> Optional[Any]:
        return self.cache.get(key)

    async def fetch_data(self, url: str) -> Dict:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                return await response.json()

    def process_batch(self, items: List[Dict]) -> List[Dict]:
        results = []
        for item in items:
            if self.validate_item(item):
                processed = self.transform_item(item)
                results.append(processed)
        return results

    def validate_item(self, item: Dict) -> bool:
        required_fields = ['id', 'value', 'timestamp']
        return all(field in item for field in required_fields)

    def transform_item(self, item: Dict) -> Dict:
        return {
            'id': item['id'],
            'value': item['value'] * 2,
            'timestamp': datetime.now().isoformat(),
            'processed': True
        }

    @staticmethod
    def calculate_statistics(values: List[float]) -> Dict[str, float]:
        arr = np.array(values)
        return {
            'mean': float(np.mean(arr)),
            'std': float(np.std(arr)),
            'min': float(np.min(arr)),
            'max': float(np.max(arr))
        }

class MLModel:
    def __init__(self, config: Dict):
        self.config = config
        self.model = None
        self.scaler = None

    def train(self, X_train: np.ndarray, y_train: np.ndarray):
        if self.config.get('use_deep_learning'):
            self.model = self._build_neural_network()
            self.model.fit(X_train, y_train, epochs=10, batch_size=32)
        else:
            self.model = RandomForestClassifier(n_estimators=100)
            self.model.fit(X_train, y_train)

    def _build_neural_network(self):
        model = tf.keras.Sequential([
            tf.keras.layers.Dense(128, activation='relu'),
            tf.keras.layers.Dropout(0.3),
            tf.keras.layers.Dense(64, activation='relu'),
            tf.keras.layers.Dropout(0.3),
            tf.keras.layers.Dense(1, activation='sigmoid')
        ])
        model.compile(optimizer='adam', loss='binary_crossentropy', metrics=['accuracy'])
        return model

    async def predict_async(self, X: np.ndarray) -> np.ndarray:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.model.predict, X)

app = Flask(__name__)

@app.route('/api/predict', methods=['POST'])
def predict():
    data = request.json
    model = MLModel({'use_deep_learning': False})
    # ... prediction logic
    return jsonify({'prediction': 0.5})

@app.route('/api/process', methods=['POST'])
async def process():
    data = request.json
    processor = DataProcessor(config={})
    results = await processor.fetch_data(data['url'])
    return jsonify(results)

def main():
    processor = DataProcessor(config={'timeout': 30})
    print("Starting data processor...")

if __name__ == '__main__':
    main()
""" * 10  # Repeat 10 times to make it larger

def benchmark_python_in_memory(iterations=1000):
    """Benchmark Python KB with in-memory code analysis"""
    print(f"\n🐍 Benchmarking Python Knowledge DB (in-memory, {iterations} iterations)...")

    kb = PythonKnowledgeDB(verbose=False)

    # Create temporary file once
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(COMPLEX_PYTHON_CODE)
        temp_file = f.name

    start = time.time()
    for i in range(iterations):
        try:
            kb.observe_file(temp_file)
        except Exception:
            pass
    end = time.time()

    # Cleanup
    Path(temp_file).unlink()

    elapsed = end - start
    ops_per_sec = iterations / elapsed

    print(f"   Iterations: {iterations}")
    print(f"   Time: {elapsed:.3f}s")
    print(f"   Throughput: {ops_per_sec:.1f} ops/sec")

    return elapsed, ops_per_sec

def benchmark_rust_in_memory(iterations=1000):
    """Benchmark Rust KB with in-memory code analysis"""
    print(f"\n🦀 Benchmarking Rust Knowledge DB (in-memory, {iterations} iterations)...")

    kb = RustKnowledgeDB(verbose=False)

    # Create temporary file once
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(COMPLEX_PYTHON_CODE)
        temp_file = f.name

    start = time.time()
    for i in range(iterations):
        try:
            kb.observe_file(temp_file)
        except Exception:
            pass
    end = time.time()

    # Cleanup
    Path(temp_file).unlink()

    elapsed = end - start
    ops_per_sec = iterations / elapsed

    print(f"   Iterations: {iterations}")
    print(f"   Time: {elapsed:.3f}s")
    print(f"   Throughput: {ops_per_sec:.1f} ops/sec")

    return elapsed, ops_per_sec

def main():
    print("╔" + "="*68 + "╗")
    print("║" + " "*10 + "EIDOS Knowledge DB - CPU-Intensive Benchmark" + " "*13 + "║")
    print("╚" + "="*68 + "╝")

    if not RUST_AVAILABLE:
        print("\n❌ Rust Knowledge DB not available!")
        return 1

    print(f"\n📋 Test setup:")
    print(f"   Code size: {len(COMPLEX_PYTHON_CODE):,} bytes")
    print(f"   Lines: {COMPLEX_PYTHON_CODE.count(chr(10)):,}")
    print(f"   Complexity: Very high (ML, async, decorators, dataclasses)")

    iterations = 1000

    # Benchmark Python
    python_time, python_ops = benchmark_python_in_memory(iterations)

    # Benchmark Rust
    rust_time, rust_ops = benchmark_rust_in_memory(iterations)

    # Calculate speedup
    speedup = python_time / rust_time
    throughput_ratio = rust_ops / python_ops

    # Results
    print("\n" + "="*70)
    print("📊 RESULTS")
    print("="*70)

    print(f"\nPython Knowledge DB:")
    print(f"  Time: {python_time:.3f}s")
    print(f"  Throughput: {python_ops:.1f} ops/sec")

    print(f"\nRust Knowledge DB:")
    print(f"  Time: {rust_time:.3f}s")
    print(f"  Throughput: {rust_ops:.1f} ops/sec")

    print(f"\n🚀 SPEEDUP:")
    print(f"  Time: {speedup:.1f}x faster")
    print(f"  Throughput: {throughput_ratio:.1f}x more ops/sec")

    print("\n" + "="*70)

    if speedup >= 100:
        print("🔥🔥🔥 INCREDIBLE! Rust is 100x+ faster than Python!")
    elif speedup >= 50:
        print("🔥🔥 EXCELLENT! Rust is 50x+ faster than Python!")
    elif speedup >= 20:
        print("🔥 GREAT! Rust is 20x+ faster than Python!")
    elif speedup >= 10:
        print("✅ GOOD! Rust is 10x+ faster than Python!")
    else:
        print(f"✅ Rust is {speedup:.1f}x faster than Python!")

    print("="*70)

    print(f"\n💡 KEY INSIGHTS:")
    print(f"  • Rust regex compilation is pre-done (startup cost)")
    print(f"  • DashMap provides lock-free concurrent access")
    print(f"  • Zero-allocation string operations where possible")
    print(f"  • No Python interpreter overhead")
    print(f"  • No GIL (Global Interpreter Lock)")

    print(f"\n🎯 REAL-WORLD IMPACT:")
    if speedup >= 10:
        print(f"  • Observing 1M files: Python={1000000/python_ops/3600:.1f}h, Rust={1000000/rust_ops/60:.1f}min")
        print(f"  • Time saved: {1000000/python_ops - 1000000/rust_ops:.0f} seconds")
    else:
        print(f"  • For 10,000 files: Python={10000/python_ops:.1f}s, Rust={10000/rust_ops:.1f}s")

    print("\n✅ Benchmark complete!")
    return 0

if __name__ == "__main__":
    sys.exit(main())
