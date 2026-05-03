CONFIG_FILE = "config.json"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10
DEFAULT_ACTIVE_POLL_SECONDS = 10
DEFAULT_BACKGROUND_POLL_SECONDS = 60
ERROR_BACKOFF_START_SECONDS = 30
ERROR_BACKOFF_MAX_SECONDS = 300
DEFAULT_IMAGE_UPLOAD_FOLDER = "chat_uploads"
DEFAULT_MAX_IMAGE_UPLOAD_MB = 5

DEFAULT_CONFIG = {
    "owner": "YOUR_GITHUB_USERNAME_OR_ORG",
    "repo": "YOUR_REPO_NAME",
    "issue_number": 1,
    "token": "YOUR_GITHUB_TOKEN",
    "display_name": "Hani",
    "github_username": "",
    "request_timeout_seconds": DEFAULT_REQUEST_TIMEOUT_SECONDS,
    "active_poll_seconds": DEFAULT_ACTIVE_POLL_SECONDS,
    "background_poll_seconds": DEFAULT_BACKGROUND_POLL_SECONDS,
    "image_upload_folder": DEFAULT_IMAGE_UPLOAD_FOLDER,
    "max_image_upload_mb": DEFAULT_MAX_IMAGE_UPLOAD_MB,
    "user_colors": {},
}


def positive_int(value, default, minimum=1):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default

    return max(minimum, value)
