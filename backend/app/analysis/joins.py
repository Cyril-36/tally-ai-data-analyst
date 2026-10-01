"""Join safety, measured on the real data (PLAN.md section 3, join rule).

A join is allowed only if the right side's key is unique (one-to-one or many-to-one), or the
spec rolls the right side up first with ``pre_aggregate``. Otherwise every left row would be
repeated once per matching right row and sums over the left table would be inflated.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlglot import exp

from app.analysis.catalog import Catalog, lookup_dataset, unknown_column_error
from app.analysis.compiler import DIALECT
from app.contracts.spec import Join

if TYPE_CHECKING:  # executor imports this module, so import it for typing only
    from app.analysis.executor import ExecOutput

# Same shape as ``executor.execute``: (sql, params, tables, extra_queries) -> ExecOutput.
Executor = Callable[[str, list[Any], dict[str, Path], list[tuple[str, list[Any]]]], "ExecOutput"]


@dataclass(frozen=True)
class JoinCheck:
    allowed: bool
    left_unique: bool
    right_unique: bool
    right_null_keys: int  # right rows whose key is null (they never match)
    orphan_left_rows: int  # left rows with no match on the right (null keys included)
    reason: str | None  # why the join was refused; None when allowed


def check_join(
    join: Join, catalog: Catalog, executor: Executor, metrics_use_right: bool = False
) -> JoinCheck:
    left = lookup_dataset(catalog, join.left.dataset)
    right = lookup_dataset(catalog, join.right.dataset)
    for ds, column in ((left, join.left.column), (right, join.right.column)):
        if column not in ds.columns:
            raise unknown_column_error(ds.name, column, ds.columns)

    lkey, rkey = join.left.column, join.right.column
    out = executor(
        _key_stats("t0", lkey),
        [],
        {"t0": left.parquet_path, "t1": right.parquet_path},
        [(_key_stats("t1", rkey), []), (_orphans(lkey, rkey), [])],
    )
    left_rows, left_nonnull, left_distinct = out.main.rows[0]
    right_rows, right_nonnull, right_distinct = out.extra[0].rows[0]
    orphans = out.extra[1].rows[0][0]

    right_unique = right_nonnull == right_distinct
    left_unique = left_nonnull == left_distinct
    reason = None
    if metrics_use_right and not left_unique:
        # Totals taken from the right side are repeated once per duplicate left key.
        reason = (
            f"{left.name}.{lkey} has duplicate values, so {right.name} figures would be "
            f"counted more than once per {lkey}. Remove the duplicates in {left.name} first."
        )
    elif not right_unique and join.pre_aggregate is None:
        reason = (
            f"Joining {left.name} to {right.name} would repeat each {left.name} row once per "
            f"matching {right.name} row and inflate totals; aggregate {right.name} per "
            f"{rkey} first (use pre_aggregate)."
        )
    return JoinCheck(
        allowed=reason is None,
        left_unique=left_unique,
        right_unique=right_unique,
        right_null_keys=right_rows - right_nonnull,
        orphan_left_rows=orphans,
        reason=reason,
    )


def _table(name: str) -> exp.Table:
    return exp.Table(this=exp.to_identifier(name, quoted=True))


def _key_stats(table: str, key: str) -> str:
    column = exp.column(key, table=table, quoted=True)
    query = exp.select(
        exp.alias_(exp.Count(this=exp.Star()), "n", quoted=True),
        exp.alias_(exp.Count(this=column.copy()), "non_null", quoted=True),
        exp.alias_(
            exp.Count(this=exp.Distinct(expressions=[column.copy()])), "distinct", quoted=True
        ),
    ).from_(_table(table))
    return query.sql(dialect=DIALECT, identify=True)


def _orphans(left_key: str, right_key: str) -> str:
    match = (
        exp.select(exp.Literal.number(1))
        .from_(_table("t1"))
        .where(
            exp.EQ(
                this=exp.column(right_key, table="t1", quoted=True),
                expression=exp.column(left_key, table="t0", quoted=True),
            )
        )
    )
    query = (
        exp.select(exp.alias_(exp.Count(this=exp.Star()), "orphans", quoted=True))
        .from_(_table("t0"))
        .where(exp.Not(this=exp.Exists(this=match)))
    )
    return query.sql(dialect=DIALECT, identify=True)
