"""
Setup para EIDOS Gateway Integration
"""

from setuptools import setup, find_packages

setup(
    name="eidos-gateway",
    version="1.0.0",
    description="EIDOS Gateway - Multi-platform messaging integration",
    packages=find_packages(),
    install_requires=[
        "aiohttp>=3.8.0",
        "python-dotenv>=0.19.0",
        "pyyaml>=5.4.0",
    ],
    entry_points={
        "console_scripts": [
            "eidos-gateway=eidos.cli.gateway_cmd:gateway_cli",
            "eidos-config=eidos.cli.config_cmd:config_cli",
        ],
    },
    python_requires=">=3.8",
)
