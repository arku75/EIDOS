#!/usr/bin/env python3
"""
Test Multimodal Learning
========================

Prueba el sistema de aprendizaje multimodal que combina:
- Audio (Whisper)
- Visual (Vision Lightweight)
- CLIP (semantic understanding)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.multimodal_learner import get_multimodal_learner

def test_youtube_video():
    """Test learning from a YouTube video"""
    print("\n" + "="*70)
    print("TEST: Multimodal Learning from YouTube Video")
    print("="*70)

    learner = get_multimodal_learner()

    # Short tutorial video (< 5 min for testing)
    test_url = "https://www.youtube.com/watch?v=5C_HPTJg5ek"  # Rust Crash Course
    title = "Rust Crash Course Tutorial"

    print(f"\n🎬 Learning from: {title}")
    print(f"   URL: {test_url}")

    try:
        knowledge = learner.learn_from_video(test_url, title)

        print("\n" + "="*70)
        print("KNOWLEDGE EXTRACTED")
        print("="*70)

        print(f"\n📝 Transcript:")
        print(f"   Length: {len(knowledge.transcript)} characters")
        if knowledge.transcript:
            preview = knowledge.transcript[:200] + "..." if len(knowledge.transcript) > 200 else knowledge.transcript
            print(f"   Preview: {preview}")

        print(f"\n🎤 Audio Events:")
        print(f"   Total: {len(knowledge.audio_events)}")
        if knowledge.audio_events:
            for i, event in enumerate(knowledge.audio_events[:3], 1):
                print(f"   {i}. [{event.timestamp:.1f}s] {event.content[:50]}...")

        print(f"\n👁️  Visual Events:")
        print(f"   Total: {len(knowledge.visual_events)}")

        print(f"\n🧠 Knowledge:")
        print(f"   Concepts: {knowledge.concepts[:10]}")  # First 10
        print(f"   Libraries: {knowledge.libraries_mentioned}")
        print(f"   Code snippets: {len(knowledge.code_snippets)}")
        print(f"   Diagrams: {len(knowledge.diagrams)}")

        print("\n✅ Test PASSED")

    except Exception as e:
        print(f"\n❌ Test FAILED: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_youtube_video()
