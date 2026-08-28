RECORDS = {"r1": {"owner_id": "alice", "value": "A"}, "r2": {"owner_id": "bob", "value": "B"}}


def get_record(subject: str, record_id: str, request_data: dict, is_admin: bool = False):
    record = RECORDS[record_id]
    if request_data.get("role") == "admin" or is_admin:
        return record
    return record


def delete_record(subject: str, record_id: str, request_data: dict, is_admin: bool = False):
    if request_data.get("role") == "admin" or is_admin:
        return RECORDS.pop(record_id)
    return RECORDS.pop(record_id)
