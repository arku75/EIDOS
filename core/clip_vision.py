#!/usr/bin/env python3
"""
EIDOS CLIP Vision
=================

Usa CLIP (Contrastive Language-Image Pre-training) de OpenAI para
entender imágenes de forma semántica.

Capabilities:
- Clasificar tipos de imágenes (diagrama, código, UI, etc)
- Búsqueda semántica en imágenes
- Detectar contenido técnico visual
- Complementar OCR con comprensión AI

CLIP vs OCR:
- OCR: Lee texto literalmente
- CLIP: Entiende QUÉ es la imagen (diagrama de arquitectura, mockup UI, etc)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Types
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ImageClassification:
    """Clasificación de una imagen"""
    category: str
    confidence: float
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ImageAnalysis:
    """Análisis completo de una imagen con CLIP"""
    image_path: str
    primary_category: ImageClassification
    all_categories: List[ImageClassification] = field(default_factory=list)
    embeddings: Optional[np.ndarray] = None  # Vector de features
    technical_content: bool = False
    confidence_score: float = 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Categorías Técnicas
# ══════════════════════════════════════════════════════════════════════════════

TECHNICAL_CATEGORIES = {
    # Architecture & Diagrams
    "architecture_diagram": "A software architecture diagram showing system components",
    "flowchart": "A flowchart diagram with boxes and arrows",
    "sequence_diagram": "A UML sequence diagram showing interactions",
    "entity_relationship": "A database ER diagram with tables and relationships",
    "network_diagram": "A network topology diagram",

    # Code & Development
    "code_screenshot": "A screenshot of source code in an IDE or editor",
    "terminal_screenshot": "A terminal or command line interface screenshot",
    "api_documentation": "API documentation with code examples",
    "code_diff": "A git diff or code comparison view",

    # UI/UX
    "ui_mockup": "A user interface mockup or wireframe",
    "ui_design": "A finished UI design or screenshot of an application",
    "ui_component": "A UI component library or design system",
    "responsive_design": "A responsive web design showing multiple screen sizes",

    # Data & Analysis
    "data_visualization": "A chart, graph, or data visualization",
    "dashboard": "A data dashboard with multiple metrics",
    "database_schema": "A database schema or table structure",
    "analytics_report": "An analytics or metrics report",

    # DevOps & Infrastructure
    "deployment_diagram": "A deployment or infrastructure diagram",
    "ci_cd_pipeline": "A CI/CD pipeline visualization",
    "cloud_architecture": "A cloud infrastructure architecture diagram",
    "container_diagram": "A Docker or Kubernetes container diagram",

    # Generic Technical
    "technical_diagram": "A technical diagram or illustration",
    "documentation_page": "A technical documentation page",
    "presentation_slide": "A technical presentation slide",

    # Non-technical (for contrast)
    "photo": "A regular photograph",
    "artwork": "Art or illustration",
    "text_document": "A plain text document or article",
}


# ══════════════════════════════════════════════════════════════════════════════
# CLIP Vision
# ══════════════════════════════════════════════════════════════════════════════

class CLIPVision:
    """
    Sistema de visión basado en CLIP para entender imágenes técnicas.

    CLIP entiende la SEMÁNTICA de las imágenes, no solo pixels o texto OCR.

    Example:
        >>> clip = CLIPVision()
        >>> analysis = clip.analyze_image("diagram.png")
        >>> print(analysis.primary_category.category)
        "architecture_diagram"
    """

    def __init__(self, model_name: str = "openai/clip-vit-base-patch32"):
        self.model_name = model_name
        self.model = None
        self.processor = None
        self.device = "cpu"  # TODO: Auto-detect GPU

        logger.info(f"🎨 CLIPVision initialized (model: {model_name})")

    def _load_model(self):
        """Load CLIP model lazily (solo cuando se necesita)"""
        if self.model is not None:
            return

        try:
            from transformers import CLIPProcessor, CLIPModel
            import torch

            logger.info("📦 Loading CLIP model...")

            self.processor = CLIPProcessor.from_pretrained(self.model_name)
            self.model = CLIPModel.from_pretrained(self.model_name)

            # Move to GPU if available
            if torch.cuda.is_available():
                self.device = "cuda"
                self.model = self.model.to(self.device)
                logger.info("✅ CLIP loaded on GPU")
            else:
                logger.info("✅ CLIP loaded on CPU")

        except ImportError:
            logger.error("❌ transformers or torch not installed!")
            logger.error("   Install: pip install --user --break-system-packages transformers torch torchvision")
            raise
        except Exception as e:
            logger.error(f"❌ Error loading CLIP: {e}")
            raise

    def analyze_image(self, image_path: str | Path, top_k: int = 5) -> ImageAnalysis:
        """
        Analiza una imagen y clasifica su contenido.

        Args:
            image_path: Ruta a la imagen
            top_k: Número de categorías top a retornar

        Returns:
            ImageAnalysis con clasificación y metadata
        """
        self._load_model()

        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        try:
            from PIL import Image
            import torch

            # Load image
            image = Image.open(image_path).convert("RGB")

            # Prepare text prompts (todas las categorías)
            text_prompts = list(TECHNICAL_CATEGORIES.values())

            # Process inputs
            inputs = self.processor(
                text=text_prompts,
                images=image,
                return_tensors="pt",
                padding=True
            )

            # Move to device
            if self.device == "cuda":
                inputs = {k: v.to(self.device) for k, v in inputs.items()}

            # Get predictions
            with torch.no_grad():
                outputs = self.model(**inputs)

            # Calculate similarity scores
            logits_per_image = outputs.logits_per_image
            probs = logits_per_image.softmax(dim=1).cpu().numpy()[0]

            # Get top K categories
            top_indices = np.argsort(probs)[::-1][:top_k]
            category_names = list(TECHNICAL_CATEGORIES.keys())

            classifications = []
            for idx in top_indices:
                cat_name = category_names[idx]
                confidence = float(probs[idx])

                classifications.append(ImageClassification(
                    category=cat_name,
                    confidence=confidence,
                    description=TECHNICAL_CATEGORIES[cat_name]
                ))

            # Primary category
            primary = classifications[0]

            # Es técnico si la confianza es alta y es una categoría técnica
            is_technical = (
                primary.confidence > 0.3 and
                primary.category not in ["photo", "artwork", "text_document"]
            )

            # Get image embeddings (útil para búsqueda semántica)
            image_embeds = outputs.image_embeds.cpu().numpy()[0]

            return ImageAnalysis(
                image_path=str(image_path),
                primary_category=primary,
                all_categories=classifications,
                embeddings=image_embeds,
                technical_content=is_technical,
                confidence_score=primary.confidence
            )

        except Exception as e:
            logger.error(f"❌ Error analyzing image: {e}")
            raise

    def classify_screenshot(self, screenshot_path: str | Path) -> str:
        """
        Clasificación rápida de screenshot.

        Returns:
            Categoría principal (str)
        """
        analysis = self.analyze_image(screenshot_path, top_k=1)
        return analysis.primary_category.category

    def is_technical_content(self, image_path: str | Path) -> bool:
        """
        Determina si una imagen contiene contenido técnico.

        Useful para filtrar capturas de pantalla relevantes.
        """
        analysis = self.analyze_image(image_path, top_k=3)
        return analysis.technical_content

    def semantic_search(self, query: str, image_paths: List[Path]) -> List[Tuple[Path, float]]:
        """
        Búsqueda semántica: encuentra imágenes que coinciden con query.

        Args:
            query: Descripción de lo que buscas (ej: "architecture diagram")
            image_paths: Lista de imágenes donde buscar

        Returns:
            Lista de (path, similarity_score) ordenada por relevancia
        """
        self._load_model()

        try:
            from PIL import Image
            import torch

            results = []

            for img_path in image_paths:
                if not img_path.exists():
                    continue

                # Load image
                image = Image.open(img_path).convert("RGB")

                # Process
                inputs = self.processor(
                    text=[query],
                    images=image,
                    return_tensors="pt",
                    padding=True
                )

                if self.device == "cuda":
                    inputs = {k: v.to(self.device) for k, v in inputs.items()}

                # Get similarity
                with torch.no_grad():
                    outputs = self.model(**inputs)
                    similarity = outputs.logits_per_image[0][0].item()

                results.append((img_path, similarity))

            # Sort by similarity (descending)
            results.sort(key=lambda x: x[1], reverse=True)

            return results

        except Exception as e:
            logger.error(f"❌ Semantic search error: {e}")
            return []

    def compare_images(self, image1: Path, image2: Path) -> float:
        """
        Compara semánticamente dos imágenes.

        Returns:
            Similarity score (0-1, higher = more similar)
        """
        self._load_model()

        try:
            import torch

            # Get embeddings for both images
            analysis1 = self.analyze_image(image1, top_k=1)
            analysis2 = self.analyze_image(image2, top_k=1)

            if analysis1.embeddings is None or analysis2.embeddings is None:
                return 0.0

            # Cosine similarity
            emb1 = torch.tensor(analysis1.embeddings)
            emb2 = torch.tensor(analysis2.embeddings)

            similarity = torch.nn.functional.cosine_similarity(
                emb1.unsqueeze(0),
                emb2.unsqueeze(0)
            ).item()

            return max(0.0, min(1.0, similarity))

        except Exception as e:
            logger.error(f"❌ Image comparison error: {e}")
            return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_clip_vision = None

def get_clip_vision() -> CLIPVision:
    """Get singleton CLIP vision instance"""
    global _clip_vision
    if _clip_vision is None:
        _clip_vision = CLIPVision()
    return _clip_vision


# ══════════════════════════════════════════════════════════════════════════════
# CLI Testing
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python clip_vision.py <image_path>")
        sys.exit(1)

    image_path = sys.argv[1]

    print("🎨 Testing CLIP Vision")
    print("="*70)

    clip = get_clip_vision()

    print(f"\n📸 Analyzing: {image_path}")

    try:
        analysis = clip.analyze_image(image_path, top_k=5)

        print("\n" + "="*70)
        print("ANALYSIS RESULTS")
        print("="*70)

        print(f"\n🎯 Primary Category:")
        print(f"   {analysis.primary_category.category}")
        print(f"   Confidence: {analysis.primary_category.confidence:.2%}")
        print(f"   Description: {analysis.primary_category.description}")

        print(f"\n📊 Top Categories:")
        for i, cat in enumerate(analysis.all_categories, 1):
            print(f"   {i}. {cat.category} ({cat.confidence:.2%})")

        print(f"\n🔬 Technical Content: {'✅ Yes' if analysis.technical_content else '❌ No'}")

        print("\n✅ Analysis complete!")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
