"""Directional portfolio overlap between two managers.

overlap(A, B) answers: what percentage of A's book, by A's weights,
is in names B also holds. The direction is part of the definition:
a whale and a minnow holding the same stocks overlap very
differently seen from each side, which is why the audit checks that
reversing the arguments changes the answer."""

from edgar13f.analysis.concentration import weighted_positions


def overlap_pct(conn, cik_a: int, cik_b: int, period: str) -> dict:
    positions_a, _ = weighted_positions(conn, cik_a, period)
    positions_b, _ = weighted_positions(conn, cik_b, period)

    shared = positions_a.index.intersection(positions_b.index)
    overlap_weight = float(positions_a.loc[shared, "weight"].sum())

    return {
        "period": period,
        "direction": f"{cik_a} -> {cik_b}",
        "overlap_pct_of_a_by_weight": round(100.0 * overlap_weight, 2),
        "n_shared_positions": len(shared),
    }
