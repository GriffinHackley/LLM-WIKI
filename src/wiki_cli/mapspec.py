"""What the CLI needs to know about `wiki map` before running it, without importing numpy.

vecmap imports numpy at its top, and the CLI is loaded by every command and agent hook
call, so the parser's choices and the error it catches live here; vecmap imports them.
"""

from __future__ import annotations

METHODS = ("auto", "tsne", "umap", "pca")
LABELS = {"tsne": "t-SNE", "umap": "UMAP", "pca": "PCA"}
COLOR_BY = ("type", "cluster", "age", "visits")


class MapError(Exception):
    pass
