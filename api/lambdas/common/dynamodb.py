"""
DynamoDB access layer for SecureCare backend.

Provides a reusable, centralised interface for all DynamoDB operations.
Table names are always read from environment configuration.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from common.config import get_config
from common.exceptions import (
    ConflictError,
    InternalError,
    NotFoundError,
)

logger = logging.getLogger("securecare.dynamodb")

# Module-level client cache (reused across Lambda invocations)
_dynamodb_resource = None
_dynamodb_client = None


def get_dynamodb_resource():
    """Return a cached DynamoDB resource."""
    global _dynamodb_resource
    if _dynamodb_resource is None:
        cfg = get_config()
        _dynamodb_resource = boto3.resource("dynamodb", region_name=cfg.AWS_REGION)
    return _dynamodb_resource


def get_dynamodb_client():
    """Return a cached DynamoDB client."""
    global _dynamodb_client
    if _dynamodb_client is None:
        cfg = get_config()
        _dynamodb_client = boto3.client("dynamodb", region_name=cfg.AWS_REGION)
    return _dynamodb_client


def get_table(table_name: str):
    """Return a DynamoDB Table resource by physical name."""
    return get_dynamodb_resource().Table(table_name)


def reset_clients():
    """Reset cached clients (for testing)."""
    global _dynamodb_resource, _dynamodb_client
    _dynamodb_resource = None
    _dynamodb_client = None


# ---------------------------------------------------------------------------
# CRUD Operations
# ---------------------------------------------------------------------------

def put_item(
    table_name: str,
    item: dict[str, Any],
    condition_expression: str | None = None,
    expression_values: dict | None = None,
    expression_names: dict | None = None,
) -> dict[str, Any]:
    """
    Put an item into a DynamoDB table.

    Args:
        table_name: Physical table name.
        item: Item data.
        condition_expression: Optional condition expression (e.g. attribute_not_exists).
        expression_values: Expression attribute values.
        expression_names: Expression attribute names.

    Returns:
        DynamoDB response.

    Raises:
        ConflictError: If the condition expression fails.
        InternalError: On unexpected DynamoDB errors.
    """
    table = get_table(table_name)
    kwargs: dict[str, Any] = {"Item": item}
    if condition_expression:
        kwargs["ConditionExpression"] = condition_expression
    if expression_values:
        kwargs["ExpressionAttributeValues"] = expression_values
    if expression_names:
        kwargs["ExpressionAttributeNames"] = expression_names

    try:
        return table.put_item(**kwargs)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ConflictError("Item already exists or condition not met")
        logger.error("DynamoDB put_item failed: %s", str(e))
        raise InternalError("Database write failed")


def get_item(
    table_name: str,
    key: dict[str, Any],
    consistent: bool = False,
    projection: str | None = None,
    expression_names: dict | None = None,
) -> dict[str, Any] | None:
    """
    Get a single item from a DynamoDB table.

    Returns:
        The item dict, or None if not found.
    """
    table = get_table(table_name)
    kwargs: dict[str, Any] = {
        "Key": key,
        "ConsistentRead": consistent,
    }
    if projection:
        kwargs["ProjectionExpression"] = projection
    if expression_names:
        kwargs["ExpressionAttributeNames"] = expression_names

    try:
        response = table.get_item(**kwargs)
        return response.get("Item")
    except ClientError as e:
        logger.error("DynamoDB get_item failed: %s", str(e))
        raise InternalError("Database read failed")


def batch_get_items(
    table_name: str,
    keys: list[dict[str, Any]],
    projection: str | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch multiple items from a table in a single BatchGetItem call.

    Args:
        table_name: Physical table name.
        keys: List of key dicts (e.g. [{"patient_id": "..."}]). Max 100.
        projection: Optional ProjectionExpression.

    Returns:
        List of found items (missing keys are silently skipped).
    """
    if not keys:
        return []
    client = get_dynamodb_client()
    found: list[dict[str, Any]] = []
    # DynamoDB accepts at most 100 keys per BatchGetItem request. Chunk rather
    # than truncating so large assignment pages hydrate completely.
    for offset in range(0, len(keys), 100):
        chunk = keys[offset : offset + 100]
        request_items: dict[str, Any] = {
            table_name: {"Keys": [_serialize_key(k) for k in chunk]}
        }
        if projection:
            request_items[table_name]["ProjectionExpression"] = projection

        attempts = 0
        while request_items and attempts < 5:
            attempts += 1
            try:
                response = client.batch_get_item(RequestItems=request_items)
            except ClientError as e:
                logger.error("DynamoDB batch_get_item failed: %s", str(e))
                raise InternalError("Database read failed")
            for _table, payload in response.get("Responses", {}).items():
                found.extend(_deserialize_item(raw_item) for raw_item in payload)
            request_items = response.get("UnprocessedKeys") or {}
            if request_items:
                time.sleep(min(0.05 * (2 ** (attempts - 1)), 0.8))
        if request_items:
            logger.warning("BatchGetItem returned unprocessed keys after retries")

    return found


def serialize_item(item: dict[str, Any]) -> dict[str, Any]:
    """Serialize a plain item dict into DynamoDB AttributeValue format.

    Public counterpart of :func:`_serialize_key`, used when building
    ``TransactWriteItems`` Put payloads.
    """
    return _serialize_key(item)


def _serialize_key(key: dict[str, Any]) -> dict[str, Any]:
    """Convert a plain Python key dict into DynamoDB AttributeValue format."""
    serialized: dict[str, Any] = {}
    for name, value in key.items():
        if isinstance(value, str):
            serialized[name] = {"S": value}
        elif isinstance(value, bool):
            serialized[name] = {"BOOL": value}
        elif isinstance(value, (int, float)):
            serialized[name] = {"N": str(value)}
        elif value is None:
            serialized[name] = {"NULL": True}
        else:
            serialized[name] = {"S": str(value)}
    return serialized


def _deserialize_item(raw_item: dict[str, Any]) -> dict[str, Any]:
    """Convert a DynamoDB AttributeValue item back into plain Python types."""
    item: dict[str, Any] = {}
    for name, value in raw_item.items():
        if "S" in value:
            item[name] = value["S"]
        elif "N" in value:
            num = value["N"]
            item[name] = float(num) if "." in num or "e" in num.lower() else int(num)
        elif "BOOL" in value:
            item[name] = value["BOOL"]
        elif "NULL" in value:
            item[name] = None
        elif "L" in value:
            item[name] = [_deserialize_item({"v": v})["v"] for v in value["L"]]
        elif "M" in value:
            item[name] = _deserialize_item(value["M"])
        elif "SS" in value:
            item[name] = list(value["SS"])
        elif "NS" in value:
            item[name] = [float(n) for n in value["NS"]]
        else:
            item[name] = value
    return item


def update_item(
    table_name: str,
    key: dict[str, Any],
    update_expression: str,
    expression_values: dict | None = None,
    expression_names: dict | None = None,
    condition_expression: str | None = None,
    return_values: str = "ALL_NEW",
) -> dict[str, Any]:
    """
    Update an item in a DynamoDB table.

    Returns:
        Updated item attributes.

    Raises:
        NotFoundError: If the item does not exist and condition fails.
        InternalError: On unexpected errors.
    """
    table = get_table(table_name)
    kwargs: dict[str, Any] = {
        "Key": key,
        "UpdateExpression": update_expression,
        "ReturnValues": return_values,
    }
    if expression_values:
        kwargs["ExpressionAttributeValues"] = expression_values
    if expression_names:
        kwargs["ExpressionAttributeNames"] = expression_names
    if condition_expression:
        kwargs["ConditionExpression"] = condition_expression

    try:
        response = table.update_item(**kwargs)
        return response.get("Attributes", {})
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise NotFoundError("Item not found or condition not met")
        logger.error("DynamoDB update_item failed: %s", str(e))
        raise InternalError("Database update failed")


def delete_item(
    table_name: str,
    key: dict[str, Any],
    condition_expression: str | None = None,
    expression_values: dict | None = None,
    expression_names: dict | None = None,
) -> dict[str, Any]:
    """
    Delete an item from a DynamoDB table.

    Returns:
        DynamoDB response.
    """
    table = get_table(table_name)
    kwargs: dict[str, Any] = {"Key": key}
    if condition_expression:
        kwargs["ConditionExpression"] = condition_expression
    if expression_values:
        kwargs["ExpressionAttributeValues"] = expression_values
    if expression_names:
        kwargs["ExpressionAttributeNames"] = expression_names

    try:
        return table.delete_item(**kwargs)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise NotFoundError("Item not found or condition not met")
        logger.error("DynamoDB delete_item failed: %s", str(e))
        raise InternalError("Database delete failed")


def query_items(
    table_name: str,
    key_condition: Any,
    index_name: str | None = None,
    filter_expression: Any | None = None,
    projection: str | None = None,
    expression_names: dict | None = None,
    scan_forward: bool = True,
    limit: int | None = None,
    exclusive_start_key: dict | None = None,
) -> dict[str, Any]:
    """
    Query a DynamoDB table or GSI.

    Returns:
        Dict with 'Items' and optional 'LastEvaluatedKey'.
    """
    table = get_table(table_name)
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": key_condition,
        "ScanIndexForward": scan_forward,
    }
    if index_name:
        kwargs["IndexName"] = index_name
    if filter_expression:
        kwargs["FilterExpression"] = filter_expression
    if projection:
        kwargs["ProjectionExpression"] = projection
    if expression_names:
        kwargs["ExpressionAttributeNames"] = expression_names
    if limit:
        kwargs["Limit"] = limit
    if exclusive_start_key:
        kwargs["ExclusiveStartKey"] = exclusive_start_key

    try:
        response = table.query(**kwargs)
        return {
            "Items": response.get("Items", []),
            "LastEvaluatedKey": response.get("LastEvaluatedKey"),
            "Count": response.get("Count", 0),
        }
    except ClientError as e:
        logger.error("DynamoDB query failed: %s", str(e))
        raise InternalError("Database query failed")


def query_all_items(
    table_name: str,
    key_condition: Any,
    index_name: str | None = None,
    filter_expression: Any | None = None,
    projection: str | None = None,
    expression_names: dict | None = None,
    scan_forward: bool = True,
    max_items: int = 1000,
) -> list[dict[str, Any]]:
    """
    Query all items with automatic pagination, up to max_items.

    Returns:
        List of items.
    """
    items: list[dict[str, Any]] = []
    start_key = None

    while len(items) < max_items:
        result = query_items(
            table_name=table_name,
            key_condition=key_condition,
            index_name=index_name,
            filter_expression=filter_expression,
            projection=projection,
            expression_names=expression_names,
            scan_forward=scan_forward,
            limit=min(100, max_items - len(items)),
            exclusive_start_key=start_key,
        )
        items.extend(result["Items"])
        start_key = result.get("LastEvaluatedKey")
        if not start_key:
            break

    return items[:max_items]


def transact_write_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Execute a DynamoDB TransactWriteItems request.

    Args:
        items: List of transaction items (Put, Update, Delete, ConditionCheck).

    Returns:
        DynamoDB response.

    Raises:
        ConflictError: If a transaction condition fails.
        InternalError: On unexpected errors.
    """
    client = get_dynamodb_client()
    try:
        return client.transact_write_items(TransactItems=items)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "TransactionCanceledException":
            reasons = e.response.get("CancellationReasons", [])
            reason_codes = {
                r.get("Code") for r in reasons if isinstance(r, dict)
            }
            logger.warning("Transaction cancelled; condition codes=%s", sorted(reason_codes))
            # Only a conditional-check failure means a business conflict
            # (e.g. the slot is already taken). Anything else is a real error.
            if "ConditionalCheckFailed" in reason_codes:
                raise ConflictError("Transaction failed due to a conflict")
            raise InternalError("Database transaction failed")
        logger.error("DynamoDB transact_write failed: %s", str(e))
        raise InternalError("Database transaction failed")
