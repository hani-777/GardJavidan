import re
import uuid

APP_COMMENT_PATTERN = re.compile(
    r"^\*\*(?P<name>.+?)\*\*\s+[—-]\s+`(?P<sent_at>[^`]+)`\s*\n\n(?P<message>.*)$",
    re.DOTALL,
)
REPLY_BLOCK_PATTERN = re.compile(
    r"^>\s+\*\*Reply to (?P<author>.+?)\*\*\s+\|\s+`(?P<time>[^`]+)`\s*\n"
    r"(?:>\s*<!--\s*reply-message-id:\s*(?P<message_id>[^>]+?)\s*-->\s*\n)?"
    r"(?:>\s*<!--\s*reply-comment-id:\s*(?P<comment_id>[^>]+?)\s*-->\s*\n)?"
    r">\s*(?P<excerpt>[^\n]*)\s*\n\n(?P<message>.*)$",
    re.DOTALL,
)
MESSAGE_ID_PATTERN = re.compile(
    r"^\s*<!--\s*chat-message-id:\s*(?P<message_id>[^\s>]+)\s*-->\s*"
    r"(?P<message>.*)$",
    re.DOTALL,
)
IMAGE_MARKDOWN_PATTERN = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<url>https?://[^\s)]+)\)"
)


def clean_reply_line(value, limit=140):
    value = " ".join((value or "").split())
    if len(value) > limit:
        return value[:limit - 1].rstrip() + "..."
    return value


def new_message_id():
    return f"ghchat-{uuid.uuid4().hex}"


def format_reply_block(reply):
    if not reply:
        return ""

    author = clean_reply_line(reply.get("author", "Unknown"), limit=60)
    sent_at = clean_reply_line(reply.get("time", ""), limit=40)
    excerpt = clean_reply_line(reply.get("excerpt", ""), limit=160)
    message_id = clean_reply_line(str(reply.get("message_id") or ""), limit=80)
    comment_id = clean_reply_line(str(reply.get("comment_id") or ""), limit=40)
    id_lines = []
    if message_id:
        id_lines.append(f"> <!-- reply-message-id: {message_id} -->")
    if comment_id:
        id_lines.append(f"> <!-- reply-comment-id: {comment_id} -->")
    metadata = "\n" + "\n".join(id_lines) if id_lines else ""
    return f"> **Reply to {author}** | `{sent_at}`{metadata}\n> {excerpt}"


def format_image_markdown(url, alt="image"):
    alt = clean_reply_line(alt, limit=80).replace("[", "").replace("]", "")
    return f"![{alt or 'image'}]({url})"


def split_image_markdown(text):
    parts = []
    position = 0
    for match in IMAGE_MARKDOWN_PATTERN.finditer(text or ""):
        if match.start() > position:
            parts.append(("text", text[position:match.start()]))
        parts.append(
            (
                "image",
                {
                    "alt": match.group("alt") or "image",
                    "url": match.group("url"),
                },
            )
        )
        position = match.end()

    if position < len(text or ""):
        parts.append(("text", text[position:]))

    return parts or [("text", text or "")]


def image_preview_text(text):
    return IMAGE_MARKDOWN_PATTERN.sub("[image]", text or "")
