import re

from setuptools import setup, find_packages

with open("documentation.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

# single source of truth for the version: Filing/__init__.py (read as text, so no import is needed)
with open("Filing/__init__.py", "r", encoding="utf-8") as fh:
    version = re.search(r'^__version__ = "([^"]+)"', fh.read(), re.M).group(1)

setup(
    name='Filer',
    version=version,
    packages=["Filing"],
    author='Demaurr',
    author_email='demaurr@example.com',
    description='A package for gathering statistics on files in any given folder and collectively moving them to any given folder.',
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/demaurr/filer",
    license="MIT",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: OS Independent",
    ],
    install_requires=[],
    entry_points={"console_scripts": ["filer=Filing.cli:main"]},
    python_requires='>=3.9',
)
