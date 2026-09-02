"""Single-table access.

Layout
------
    PK=REQ#{id}            SK=META | NEED | GEO | MATCH
    PK=RES#{id}            SK=META
    PK=DEC#{id}            SK=META
    PK=AUDIT#{yyyy-mm-dd}  SK={ts}#{id}
    PK=GEO#{norm_query}    SK=CACHE
    GSI1: gsi1pk=STATUS#{status}, gsi1sk={inverted_urgency}#{id}  -- the board query

Items carry their payload as a JSON string in `body`, with only the attributes
actually used for indexing or filtering promoted to top-level scalars. This is
deliberate: DynamoDB coerces floats to Decimal, and urgency scores round-
tripping through Decimal is exactly the kind of silent drift this project
cannot afford. See docs/decisions.md.

Two backends sit behind one interface. `DynamoBackend` is the real thing;
`MemoryBackend` is an in-process dict used by the test suite so unit tests need
no Docker. The backend in use is reported by /healthz and on the About screen --
it is never presented as something it is not.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Protocol

from ..config import Settings

Item = dict[str, Any]


# --------------------------------------------------------------------------
# Key builders. Centralised so no route or repo ever hand-formats a key.
# --------------------------------------------------------------------------

def req_pk(request_id: str) -> str:
    return f"REQ#{request_id}"


def res_pk(resource_id: str) -> str:
    return f"RES#{resource_id}"


def dec_pk(decision_id: str) -> str:
    return f"DEC#{decision_id}"


def audit_pk(day: str) -> str:
    return f"AUDIT#{day}"


def geo_pk(normalised_query: str) -> str:
    return f"GEO#{normalised_query}"


def board_gsi1pk(status: str) -> str:
    return f"STATUS#{status}"


def board_gsi1sk(urgency: float | None, request_id: str) -> str:
    """Sortable urgency key.

    Zero-padded to a fixed width so lexicographic order matches numeric order,
    and inverted so that a plain forward query returns most-urgent-first.
    """
    u = 0.0 if urgency is None else max(0.0, min(1.0, urgency))
    return f"{1.0 - u:.4f}#{request_id}"


class Backend(Protocol):
    def put(self, item: Item) -> None: ...

    def get(self, pk: str, sk: str) -> Item | None: ...

    def delete(self, pk: str, sk: str) -> None: ...

    def query(
        self, pk: str, *, sk_prefix: str | None = None, limit: int | None = None
    ) -> list[Item]: ...

    def query_gsi1(self, gsi1pk: str, *, limit: int | None = None) -> list[Item]: ...

    def scan_prefix(self, pk_prefix: str, *, limit: int | None = None) -> list[Item]: ...

    def clear(self) -> None: ...

    @property
    def label(self) -> str: ...


def _copy(value: Any) -> Any:
    """Defensive deep copy, so callers cannot mutate stored state in place."""
    return json.loads(json.dumps(value))


class MemoryBackend:
    """In-process store. Thread-safe, ordered, and honest about being local."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Item] = {}
        self._lock = threading.RLock()

    @property
    def label(self) -> str:
        return "memory"

    def put(self, item: Item) -> None:
        with self._lock:
            self._items[(item["pk"], item["sk"])] = _copy(item)

    def get(self, pk: str, sk: str) -> Item | None:
        with self._lock:
            found = self._items.get((pk, sk))
            return _copy(found) if found else None

    def delete(self, pk: str, sk: str) -> None:
        with self._lock:
            self._items.pop((pk, sk), None)

    def query(
        self, pk: str, *, sk_prefix: str | None = None, limit: int | None = None
    ) -> list[Item]:
        with self._lock:
            keys = sorted(k for k in self._items if k[0] == pk)
            rows = [
                _copy(self._items[k])
                for k in keys
                if sk_prefix is None or k[1].startswith(sk_prefix)
            ]
        return rows[:limit] if limit else rows

    def query_gsi1(self, gsi1pk: str, *, limit: int | None = None) -> list[Item]:
        with self._lock:
            rows = [_copy(i) for i in self._items.values() if i.get("gsi1pk") == gsi1pk]
        rows.sort(key=lambda i: str(i.get("gsi1sk", "")))
        return rows[:limit] if limit else rows

    def scan_prefix(self, pk_prefix: str, *, limit: int | None = None) -> list[Item]:
        with self._lock:
            keys = sorted(k for k in self._items if k[0].startswith(pk_prefix))
            rows = [_copy(self._items[k]) for k in keys]
        return rows[:limit] if limit else rows

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class DynamoBackend:
    """boto3-backed single table (DynamoDB Local or real DynamoDB)."""

    def __init__(self, table_name: str, region: str, endpoint: str | None) -> None:
        import boto3  # lazy: unit tests never need boto3 configured

        self._name = table_name
        self._endpoint = endpoint
        kwargs: dict[str, Any] = {"region_name": region}
        if endpoint:
            kwargs["endpoint_url"] = endpoint
            # DynamoDB Local accepts any credentials, but botocore requires some.
            kwargs.setdefault("aws_access_key_id", "local")
            kwargs.setdefault("aws_secret_access_key", "local")
        self._ddb = boto3.resource("dynamodb", **kwargs)
        self._table = self._ddb.Table(table_name)

    @property
    def label(self) -> str:
        return f"dynamodb({self._endpoint or 'aws'})"

    def ensure_table(self) -> None:
        """Create the table and GSI1 if absent. Used against DynamoDB Local."""
        from botocore.exceptions import ClientError

        try:
            self._table.load()
            return
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceNotFoundException":
                raise
        self._ddb.create_table(
            TableName=self._name,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": "gsi1pk", "AttributeType": "S"},
                {"AttributeName": "gsi1sk", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "gsi1pk", "KeyType": "HASH"},
                        {"AttributeName": "gsi1sk", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        self._table.wait_until_exists()

    def put(self, item: Item) -> None:
        self._table.put_item(Item=item)

    def get(self, pk: str, sk: str) -> Item | None:
        got = self._table.get_item(Key={"pk": pk, "sk": sk}).get("Item")
        return dict(got) if got else None

    def delete(self, pk: str, sk: str) -> None:
        self._table.delete_item(Key={"pk": pk, "sk": sk})

    def query(
        self, pk: str, *, sk_prefix: str | None = None, limit: int | None = None
    ) -> list[Item]:
        from boto3.dynamodb.conditions import Key

        cond: Any = Key("pk").eq(pk)
        if sk_prefix:
            cond = cond & Key("sk").begins_with(sk_prefix)
        kwargs: dict[str, Any] = {"KeyConditionExpression": cond}
        if limit:
            kwargs["Limit"] = limit
        return [dict(i) for i in self._table.query(**kwargs).get("Items", [])]

    def query_gsi1(self, gsi1pk: str, *, limit: int | None = None) -> list[Item]:
        from boto3.dynamodb.conditions import Key

        kwargs: dict[str, Any] = {
            "IndexName": "GSI1",
            "KeyConditionExpression": Key("gsi1pk").eq(gsi1pk),
            "ScanIndexForward": True,
        }
        if limit:
            kwargs["Limit"] = limit
        return [dict(i) for i in self._table.query(**kwargs).get("Items", [])]

    def scan_prefix(self, pk_prefix: str, *, limit: int | None = None) -> list[Item]:
        from boto3.dynamodb.conditions import Attr

        kwargs: dict[str, Any] = {"FilterExpression": Attr("pk").begins_with(pk_prefix)}
        if limit:
            kwargs["Limit"] = limit
        return [dict(i) for i in self._table.scan(**kwargs).get("Items", [])]

    def clear(self) -> None:
        rows = self._table.scan().get("Items", [])
        with self._table.batch_writer() as batch:
            for row in rows:
                batch.delete_item(Key={"pk": row["pk"], "sk": row["sk"]})


class Table:
    """Thin facade the repositories use."""

    def __init__(self, backend: Backend) -> None:
        self.backend = backend

    @property
    def label(self) -> str:
        return self.backend.label

    def put_model(
        self,
        pk: str,
        sk: str,
        payload: dict[str, Any],
        *,
        gsi1pk: str | None = None,
        gsi1sk: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        item: Item = {"pk": pk, "sk": sk, "body": json.dumps(payload, separators=(",", ":"))}
        if gsi1pk is not None and gsi1sk is not None:
            item["gsi1pk"] = gsi1pk
            item["gsi1sk"] = gsi1sk
        if extra:
            item.update(extra)
        self.backend.put(item)

    @staticmethod
    def body(item: Item | None) -> dict[str, Any] | None:
        if item is None:
            return None
        raw = item.get("body")
        if not raw:
            return None
        parsed: dict[str, Any] = json.loads(raw)
        return parsed

    def get_body(self, pk: str, sk: str) -> dict[str, Any] | None:
        return self.body(self.backend.get(pk, sk))

    def bodies(self, items: list[Item]) -> list[dict[str, Any]]:
        return [b for b in (self.body(i) for i in items) if b is not None]


_table: Table | None = None
_table_lock = threading.Lock()


def build_table(settings: Settings) -> Table:
    """Pick a backend. An unset or `memory` DDB_ENDPOINT selects the in-process store."""
    endpoint = settings.ddb_endpoint
    if endpoint is None or endpoint.lower() == "memory":
        return Table(MemoryBackend())
    backend = DynamoBackend(settings.ddb_table, settings.aws_region, endpoint)
    backend.ensure_table()
    return Table(backend)


def get_table(settings: Settings | None = None) -> Table:
    global _table
    if _table is None:
        with _table_lock:
            if _table is None:
                from ..config import get_settings

                _table = build_table(settings or get_settings())
    return _table


def reset_table(table: Table | None = None) -> None:
    """Test and demo hook: swap or drop the process-wide table."""
    global _table
    with _table_lock:
        _table = table
