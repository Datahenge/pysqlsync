import abc
from typing import Callable, Mapping, Optional, TypeVar

from ..model.id_types import SupportsName
from .data_types import constant
from .object_types import (
    Catalog,
    Column,
    ColumnFormationError,
    DatabaseObject,
    EnumType,
    FormationError,
    Namespace,
    StructType,
    Table,
    TableFormationError,
)

T = TypeVar("T", bound=DatabaseObject)


class Mutator(abc.ABC):
    def check_identity(self, source: SupportsName, target: SupportsName) -> None:
        if source.name != target.name:
            raise FormationError(f"object mismatch: {source.name} != {target.name}")

    def mutate_enum_stmt(self, source: EnumType, target: EnumType) -> Optional[str]:
        self.check_identity(source, target)

        removed_values = [
            value for value in source.values if value not in target.values
        ]
        if removed_values:
            raise FormationError(
                f"operation not permitted; cannot drop values in an enumeration: {''.join(removed_values)}"
            )

        added_values = [value for value in target.values if value not in source.values]
        if added_values:
            return (
                f"ALTER TYPE {source.name}\n"
                + ",\n".join(f"ADD VALUE {constant(v)}" for v in added_values)
                + ";"
            )
        else:
            return None

    def mutate_struct_stmt(
        self, source: StructType, target: StructType
    ) -> Optional[str]:
        self.check_identity(source, target)

        statements: list[str] = []
        for source_member in source.members.values():
            if source_member not in target.members.values():
                statements.append(f"DROP ATTRIBUTE {source_member.name}")
        for target_member in target.members.values():
            if target_member not in target.members.values():
                statements.append(f"ADD ATTRIBUTE {target_member}")
        if statements:
            return f"ALTER TYPE {source.name}\n" + ",\n".join(statements) + ";\n"
        else:
            return None

    def mutate_column_stmt(self, source: Column, target: Column) -> Optional[str]:
        if source == target:
            return None

        statements: list[str] = []

        if source.data_type != target.data_type:
            statements.append(f"SET DATA TYPE {target.data_type}")

        if source.nullable and not target.nullable:
            statements.append("SET NOT NULL")
        elif not source.nullable and target.nullable:
            statements.append("DROP NOT NULL")

        if source.default is not None and target.default is None:
            statements.append("DROP DEFAULT")
        elif source.default != target.default:
            statements.append(f"SET DEFAULT {target.default}")

        if source.identity and not target.identity:
            statements.append("DROP IDENTITY")
        elif not source.identity and target.identity:
            statements.append("ADD GENERATED BY DEFAULT AS IDENTITY")

        if statements:
            return ",\n".join(f"ALTER COLUMN {source.name} {s}" for s in statements)
        else:
            return None

    def mutate_table_stmt(self, source: Table, target: Table) -> Optional[str]:
        self.check_identity(source, target)

        statements: list[str] = []
        source_column: Optional[Column]
        try:
            for target_column in target.columns.values():
                source_column = source.columns.get(target_column.name.id)
                if source_column is None:
                    statements.append(target_column.create_stmt())
                else:
                    statement = self.mutate_column_stmt(source_column, target_column)
                    if statement:
                        statements.append(statement)
        except ColumnFormationError as e:
            raise TableFormationError(
                "failed to create or update columns in table", target.name
            ) from e

        try:
            for source_column in source.columns.values():
                if source_column.name.id not in target.columns:
                    statements.append(source_column.drop_stmt())
        except ColumnFormationError as e:
            raise TableFormationError(
                "failed to drop columns in table", target.name
            ) from e

        if source.constraints and not target.constraints:
            for constraint in source.constraints:
                if constraint.is_alter_table():
                    statements.append(f"DROP CONSTRAINT {constraint.name}")
        elif not source.constraints and target.constraints:
            for constraint in target.constraints:
                if constraint.is_alter_table():
                    statements.append(f"ADD CONSTRAINT {constraint.spec}")
        elif source.constraints and target.constraints:
            for target_constraint in target.constraints:
                ...

        if statements:
            return source.alter_table_stmt(statements)
        else:
            return None

    def mutate_namespace_stmt(
        self, source: Namespace, target: Namespace
    ) -> Optional[str]:
        self.check_identity(source, target)

        statements: list[str] = []

        statements.extend(_create_diff(source.enums, target.enums))
        statements.extend(_create_diff(source.structs, target.structs))
        statements.extend(_create_diff(source.tables, target.tables))

        for id in target.tables.keys():
            if id not in source.tables.keys():
                statement = target.tables[id].add_constraints_stmt()
                if statement:
                    statements.append(statement)

        statements.extend(
            _mutate_diff(self.mutate_enum_stmt, source.enums, target.enums)
        )
        statements.extend(
            _mutate_diff(self.mutate_struct_stmt, source.structs, target.structs)
        )
        statements.extend(
            _mutate_diff(self.mutate_table_stmt, source.tables, target.tables)
        )

        statements.extend(_drop_diff(source.tables, target.tables))
        statements.extend(_drop_diff(source.structs, target.structs))
        statements.extend(_drop_diff(source.enums, target.enums))

        return "\n".join(statements) if statements else None

    def mutate_catalog_stmt(self, source: Catalog, target: Catalog) -> Optional[str]:
        statements: list[str] = []
        statements.extend(_create_diff(source.namespaces, target.namespaces))
        for id in target.namespaces.keys():
            if id not in source.namespaces.keys():
                statement = target.namespaces[id].add_constraints_stmt()
                if statement:
                    statements.append(statement)

        statements.extend(
            _mutate_diff(
                self.mutate_namespace_stmt, source.namespaces, target.namespaces
            )
        )

        for id in source.namespaces.keys():
            if id not in target.namespaces.keys():
                statement = source.namespaces[id].drop_constraints_stmt()
                if statement:
                    statements.append(statement)
        statements.extend(_drop_diff(source.namespaces, target.namespaces))
        return "\n".join(statements) if statements else None


def _create_diff(source: Mapping[str, T], target: Mapping[str, T]) -> list[str]:
    return [target[id].create_stmt() for id in target.keys() if id not in source.keys()]


def _drop_diff(source: Mapping[str, T], target: Mapping[str, T]) -> list[str]:
    return [source[id].drop_stmt() for id in source.keys() if id not in target.keys()]


def _mutate_diff(
    mutate_fn: Callable[[T, T], Optional[str]],
    source: Mapping[str, T],
    target: Mapping[str, T],
) -> list[str]:
    statements: list[str] = []

    for id in source.keys():
        if id in target.keys():
            statement = mutate_fn(source[id], target[id])
            if statement:
                statements.append(statement)

    return statements
