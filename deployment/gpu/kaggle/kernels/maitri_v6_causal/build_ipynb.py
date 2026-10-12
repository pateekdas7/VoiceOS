"""Wrap source.py into a single-cell notebook."""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
SRC  = os.path.join(HERE, "source.py")
OUT  = os.path.join(HERE, "voiceos-maitri-causal-analysis-v6.ipynb")
with open(SRC) as f:
    source = f.read()
nb = {"cells": [{"cell_type": "code", "execution_count": None, "metadata": {},
      "outputs": [], "source": source.splitlines(keepends=True)}],
      "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
      "language_info": {"name": "python", "version": "3.11"}}, "nbformat": 4, "nbformat_minor": 5}
with open(OUT, "w") as f:
    json.dump(nb, f)
print(f"wrote {OUT} size={os.path.getsize(OUT)}")
