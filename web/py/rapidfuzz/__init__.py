"""Pure-Python stand-in for the parts of rapidfuzz that `vericode.risk` uses.

Pyodide does not ship rapidfuzz, so the web build bundles this instead. Only
`process.extract(..., scorer=Levenshtein.distance, score_cutoff=N, limit=K)` is supported.
"""
