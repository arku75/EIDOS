"""
Packaging for the independently installable EIDOS gateway/config CLI surface.

Committee review (2026-10-04):
- Architecture: keep this distribution scoped to gateway/config; the full EIDOS
  runtime has a broader dependency and hardware surface.
- Verification: CI installs the package without dependencies and exercises both
  console-script help paths from outside the checkout.
- Security/data: installation must not require secrets, private databases,
  network services, or writes to the live EIDOS home.
- Maintainability: reuse the existing cli/, config/ and gateway/ packages rather
  than introducing a parallel CLI implementation.
- EIDOS vision: packaging is infrastructure only; it does not count as causal
  integration or as verified action success.
"""

from setuptools import find_packages, setup


setup(
    name="eidos-gateway",
    version="1.0.0",
    description="Installable EIDOS gateway and configuration CLI surface",
    packages=find_packages(
        include=[
            "cli",
            "cli.*",
            "config",
            "config.*",
            "gateway",
            "gateway.*",
        ]
    ),
    install_requires=[
        "aiohttp>=3.8.0",
        "python-dotenv>=0.19.0",
        "PyYAML>=5.4.0",
    ],
    entry_points={
        "console_scripts": [
            "eidos-gateway=cli.gateway_cmd:gateway_cli",
            "eidos-config=cli.config_cmd:config_cli",
        ],
    },
    python_requires=">=3.11",
)
