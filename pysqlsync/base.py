import abc
import dataclasses
from dataclasses import dataclass
from io import StringIO
from typing import Any, Callable, Iterable, Optional, TextIO, TypeVar

from strong_typing.inspection import is_dataclass_type, is_type_enum

T = TypeVar("T")


def _get_extractor(field_name: str, field_type: type) -> Callable[[Any], Any]:
    if is_type_enum(field_type):
        return lambda obj: getattr(obj, field_name).value
    else:
        return lambda obj: getattr(obj, field_name)


def _get_extractors(class_type: type) -> tuple[Callable[[Any], Any], ...]:
    return tuple(
        _get_extractor(field.name, field.type)
        for field in dataclasses.fields(class_type)
    )


class BaseGenerator(abc.ABC):
    "Generates SQL statements for creating or dropping tables, and inserting, updating or deleting data."

    cls: type

    def __init__(self, cls: type) -> None:
        self.cls = cls

    @abc.abstractmethod
    def write_create_table_stmt(self, target: TextIO) -> None:
        ...

    @abc.abstractmethod
    def write_upsert_stmt(self, target: TextIO) -> None:
        ...

    def get_create_table_stmt(self) -> str:
        s = StringIO()
        self.write_create_table_stmt(s)
        return s.getvalue()

    def get_upsert_stmt(self) -> str:
        s = StringIO()
        self.write_upsert_stmt(s)
        return s.getvalue()

    def get_record_as_tuple(self, obj: Any) -> tuple:
        extractors = _get_extractors(self.cls)
        return tuple(extractor(obj) for extractor in extractors)

    def get_records_as_tuples(self, items: Iterable[Any]) -> list[tuple]:
        extractors = _get_extractors(self.cls)
        return [tuple(extractor(item) for extractor in extractors) for item in items]


@dataclass
class Parameters:
    "Database connection parameters that would typically be encapsulated in a connection string."

    host: Optional[str]
    port: Optional[int]
    username: Optional[str]
    password: Optional[str]
    database: Optional[str]


class BaseConnection(abc.ABC):
    "An active connection to a database."

    generator_type: type[BaseGenerator]
    params: Parameters

    def __init__(self, generator_type: type[BaseGenerator], params: Parameters) -> None:
        self.generator_type = generator_type
        self.params = params

    @abc.abstractmethod
    async def __aenter__(self) -> "BaseContext":
        ...

    @abc.abstractmethod
    async def __aexit__(self, exc_type, exc, tb) -> None:
        ...


def check_dataclass_type(table: type) -> None:
    if not is_dataclass_type(table):
        raise TypeError(f"expected dataclass type, got: {table}")


class BaseContext(abc.ABC):
    "Context object returned by a connection object."

    connection: BaseConnection

    def __init__(self, connection: BaseConnection) -> None:
        self.connection = connection

    @abc.abstractmethod
    async def execute(self, statement: str) -> None:
        ...

    @abc.abstractmethod
    async def execute_all(
        self, statement: str, args: Iterable[tuple[Any, ...]]
    ) -> None:
        ...

    async def drop_table(self, table: type, ignore_missing: bool = False) -> None:
        "Drops a database table corresponding to the dataclass type."

        check_dataclass_type(table)
        table_name = table.__name__

        if ignore_missing:
            statement = f'DROP TABLE IF EXISTS "{table_name}"'
        else:
            statement = f'DROP TABLE "{table_name}"'
        await self.execute(statement)

    async def create_table(self, table: type) -> None:
        "Creates a database table for storing data encapsulated in the dataclass type."

        check_dataclass_type(table)
        generator = self.connection.generator_type(table)
        statement = generator.get_create_table_stmt()
        await self.execute(statement)

    async def insert_data(self, table: type[T], data: Iterable[T]) -> None:
        return await self.upsert_data(table, data)

    async def upsert_data(self, table: type[T], data: Iterable[T]) -> None:
        "Inserts or updates data in the database table corresponding to the dataclass type."

        generator = self.connection.generator_type(table)
        statement = generator.get_upsert_stmt()
        records = generator.get_records_as_tuples(data)
        await self.execute_all(statement, records)


class BaseEngine(abc.ABC):
    "Represents a specific database server type."

    @abc.abstractmethod
    def get_generator_type(self) -> type[BaseGenerator]:
        ...

    @abc.abstractmethod
    def get_connection_type(self) -> type[BaseConnection]:
        ...

    def create_connection(self, params: Parameters) -> BaseConnection:
        generator_type = self.get_generator_type()
        connection_type = self.get_connection_type()
        return connection_type(generator_type, params)

    def create_generator(self, cls: type) -> BaseGenerator:
        generator_type = self.get_generator_type()
        return generator_type(cls)
