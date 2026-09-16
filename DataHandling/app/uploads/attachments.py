"""URL-only persistence with compatibility for older attachment metadata."""


def attachment_urls(attachments):
    urls = []
    for attachment in attachments or []:
        url = attachment if isinstance(attachment, str) else attachment.get("url") if isinstance(attachment, dict) else None
        if isinstance(url, str) and url.strip() and url not in urls:
            urls.append(url)
    return urls


def append_attachment_urls_expression(urls):
    """Normalize legacy records and append URLs atomically, without lost uploads."""
    return {"$setUnion": [
        {"$filter": {
            "input": {"$map": {
                "input": {"$ifNull": ["$attachments", []]},
                "as": "attachment",
                "in": {"$cond": [
                    {"$eq": [{"$type": "$$attachment"}, "string"]},
                    "$$attachment", {"$ifNull": ["$$attachment.url", None]},
                ]},
            }},
            "as": "url",
            "cond": {"$and": [
                {"$eq": [{"$type": "$$url"}, "string"]},
                {"$ne": ["$$url", ""]},
            ]},
        }},
        {"$literal": attachment_urls(urls)},
    ]}
