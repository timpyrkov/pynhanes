import os
from setuptools import setup, find_packages


def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()

setup(
    name="pynhanes",
    version="0.0.19",
    author="Tim Pyrkov",
    author_email="tim.pyrkov@gmail.com",
    description="Python parser and scraper for NHANES accelerometry and questionnaire",
    long_description=read("README.md"),
    license = "MIT License",
    long_description_content_type="text/markdown",
    url="https://github.com/timpyrkov/pynhanes",
    packages=find_packages(exclude=("docs")),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Topic :: Scientific/Engineering",
    ],
    python_requires=">=3.6",
    include_package_data=True,
    install_requires=[
        "jsoncomment",
        "numpy",
        "scipy",
        "matplotlib",
        "seaborn",
        "statannotations",
        "pandas",
        "requests",
        "tqdm"
    ],
    # tests_require=["flake8", "pytest"],
    # setup_requires=["pytest-runner", ],
    # scripts=["scripts/wgetxpt.py",],
    entry_points={
        "console_scripts": [
            "pywgetxpt = pynhanes.wgetxpt:main",
        ]
    }
)

