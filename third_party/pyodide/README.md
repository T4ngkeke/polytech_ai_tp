# Pyodide

The frontend image serves Pyodide 0.26.4 and a few Python packages from
`/pyodide/` (downloaded at build time by `frontend/scripts/fetch-pyodide.mjs`,
each file checked against its sha256). The files are not stored in this
repository and are not modified.

- Pyodide: Mozilla Public License 2.0. Source: https://github.com/pyodide/pyodide (tag 0.26.4).
- The packages keep their own licenses, listed in each wheel's metadata:
  numpy, scipy, pandas, scikit-learn, statsmodels, networkx, patsy (BSD);
  matplotlib (Matplotlib license, PSF-based); sympy (BSD); mpmath (BSD);
  pillow (HPND / MIT-CMU); fonttools, pyparsing, six, packaging, python-dateutil,
  pytz, cycler, kiwisolver, threadpoolctl, joblib, decorator, setuptools
  (MIT, BSD or Apache 2.0); OpenBLAS (BSD); CPython and its sqlite3 module (PSF,
  SQLite is public domain).
