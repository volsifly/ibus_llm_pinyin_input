def merge_candidates(*candidate_groups, limit=5):
    result = []
    seen = set()
    unlimited = limit is None or limit <= 0
    for group in candidate_groups:
        for item in group or []:
            text = item.get("text") if isinstance(item, dict) else item
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
            if not unlimited and len(result) >= limit:
                return result
    return result
