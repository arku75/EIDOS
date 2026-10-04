"""Installable EIDOS core plus gateway/config CLI surface.

The base distribution deliberately keeps heavyweight model/vision stacks
optional.  A fresh Debian-family system can install the persistent EIDOS core,
shared terminal and deterministic acceptance tools without Torch, Transformers,
Ollama or any remote model provider.  Specialized organs remain capability-
gated and may require the broader requirements.txt stack.
"""

from setuptools import find_packages, setup


setup(
    name="eidos-gateway",
    version="1.1.0",
    description="Installable EIDOS core runtime, shared CLI, gateway and config tools",
    packages=find_packages(
        include=[
            "core",
            "core.*",
            "bin",
            "bin.*",
            "cli",
            "cli.*",
            "config",
            "config.*",
            "gateway",
            "gateway.*",
            "terminal",
            "terminal.*",
            "api",
            "api.*",
            "browser",
            "browser.*",
            "daemon",
            "daemon.*",
        ]
    ),
    py_modules=["eidos"],
    install_requires=[
        "aiohttp>=3.8.0",
        "python-dotenv>=0.19.0",
        "PyYAML>=5.4.0",
    ],
    entry_points={
        "console_scripts": [
            "eidos=eidos:main",
            "eidos-gateway=cli.gateway_cmd:gateway_cli",
            "eidos-config=cli.config_cmd:config_cli",
            "eidos-acceptance-zero-llm=core.zero_llm_acceptance:main",
        ],
    },
    python_requires=">=3.11",
)
