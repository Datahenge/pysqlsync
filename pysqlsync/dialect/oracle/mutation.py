import typing
from typing import Optional

from pysqlsync.formation.mutation import Mutator
from pysqlsync.formation.object_types import Column

from .object_types import OracleColumn


class OracleMutator(Mutator):
    def mutate_column_stmt(
        self, source_column: Column, target_column: Column
    ) -> Optional[str]:
        source = typing.cast(OracleColumn, source_column)
        target = typing.cast(OracleColumn, target_column)

        changes: list[str] = []
        if source.data_type != target.data_type:
            changes.append(str(target.data_type))
        if source.default != target.default:
            if target.default is not None:
                changes.append(f"DEFAULT {target.default_expr}")
            else:
                changes.append("DEFAULT NULL")
        if source.nullable != target.nullable:
            if not target.nullable:
                changes.append("NOT NULL")
            else:
                changes.append("NULL")
        if source.identity != target.identity:
            if target.identity:
                changes.append("GENERATED BY DEFAULT AS IDENTITY")
            else:
                changes.append("DROP IDENTITY")

        if changes:
            return f"MODIFY {source.name} {' '.join(changes)}"
        else:
            return None
