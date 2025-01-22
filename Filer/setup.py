from setuptools import setup, find_packages

with open("documentation.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name='Filer',
    version='1.2.1',
    packages=["Filing"],
    author='Demaurr',
    author_email='demaurr@example.com',
    description='A package for gathering statistics on files in any given folder and collectively moving them to any given folder.',
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/demaurr/filer",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
    ],
    install_requires=[],
    python_requires='>=3.6',
)
