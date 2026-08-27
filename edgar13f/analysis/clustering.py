"""Clusters managers by portfolio similarity.

Each manager-quarter becomes a weight vector over CUSIPs; cosine
similarity compares vectors; average-linkage hierarchical clustering
groups managers. With few managers this is a demonstration of the
machinery rather than a statistical finding, and the README says so
plainly."""

import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from sklearn.metrics.pairwise import cosine_similarity

from edgar13f.analysis.concentration import weighted_positions


def weight_matrix(conn, ciks: list[int], period: str) -> pd.DataFrame:
    """Managers as rows, CUSIPs as columns, portfolio weights as
    values. Missing positions are zero weight."""
    columns = {}
    for cik in ciks:
        positions, _ = weighted_positions(conn, cik, period)
        columns[cik] = positions["weight"]
    return pd.DataFrame(columns).T.fillna(0.0)


def similarity_matrix(conn, ciks: list[int], period: str) -> pd.DataFrame:
    matrix = weight_matrix(conn, ciks, period)
    similarities = cosine_similarity(matrix.values)
    return pd.DataFrame(similarities, index=matrix.index, columns=matrix.index)


def cluster_managers(conn, ciks: list[int], period: str,
                     distance_threshold: float = 0.7) -> dict[int, int]:
    """Cluster labels per CIK. Distance is 1 - cosine similarity;
    average linkage; threshold chosen so near-disjoint books
    (similarity < 0.3) split apart."""
    sim = similarity_matrix(conn, ciks, period)
    distance = 1.0 - sim.values
    condensed = distance[np_triu_indices(len(ciks))]
    tree = linkage(condensed, method="average")
    labels = fcluster(tree, t=distance_threshold, criterion="distance")
    return dict(zip(sim.index, labels))


def np_triu_indices(n: int):
    import numpy as np
    return np.triu_indices(n, k=1)
