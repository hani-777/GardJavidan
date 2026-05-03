import base64
import os
import re
import time
from datetime import datetime
from urllib.parse import quote

import requests

from config import (
    DEFAULT_IMAGE_UPLOAD_FOLDER,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    positive_int,
)
from message_format import clean_reply_line, format_reply_block, new_message_id

SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


class GitHubApiError(Exception):
    def __init__(
        self,
        message,
        *,
        rate_limited=False,
        rate_info=None,
        retry_after_seconds=None,
    ):
        super().__init__(message)
        self.rate_limited = rate_limited
        self.rate_info = rate_info or {}
        self.retry_after_seconds = retry_after_seconds


class GitHubIssueChat:
    def __init__(self, config):
        self.owner = config["owner"]
        self.repo = config["repo"]
        self.issue_number = int(config["issue_number"])
        self.token = config["token"]
        self.display_name = config.get("display_name", "User")
        self.timeout = positive_int(
            config.get("request_timeout_seconds"),
            DEFAULT_REQUEST_TIMEOUT_SECONDS,
        )
        self.repo_url = f"https://api.github.com/repos/{self.owner}/{self.repo}"
        self.contents_url = f"{self.repo_url}/contents"
        self.set_issue_number(self.issue_number)
        self.image_upload_folder = self.clean_upload_folder(
            config.get("image_upload_folder", DEFAULT_IMAGE_UPLOAD_FOLDER)
        )
        self.user_url = "https://api.github.com/user"
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def issue_comments_url(self, issue_number=None):
        issue_number = int(issue_number or self.issue_number)
        return f"{self.repo_url}/issues/{issue_number}/comments"

    def issue_api_url(self, issue_number=None):
        issue_number = int(issue_number or self.issue_number)
        return f"{self.repo_url}/issues/{issue_number}"

    def set_issue_number(self, issue_number):
        self.issue_number = int(issue_number)
        self.base_url = self.issue_comments_url(self.issue_number)
        self.issue_url = self.issue_api_url(self.issue_number)

    @staticmethod
    def rate_info_from_response(response):
        reset_epoch = response.headers.get("X-RateLimit-Reset")
        reset_at = ""
        reset_seconds = None

        if reset_epoch:
            try:
                reset_seconds = max(0, int(reset_epoch) - int(time.time()))
                reset_at = datetime.fromtimestamp(int(reset_epoch)).strftime("%H:%M:%S")
            except ValueError:
                reset_seconds = None

        return {
            "remaining": response.headers.get("X-RateLimit-Remaining", "?"),
            "reset_at": reset_at,
            "reset_seconds": reset_seconds,
            "retry_after": response.headers.get("Retry-After"),
        }

    @staticmethod
    def response_message(response):
        try:
            data = response.json()
            message = data.get("message", "") if isinstance(data, dict) else ""
        except ValueError:
            message = response.text.strip()

        return " ".join(message.split())[:220]

    @staticmethod
    def is_rate_limited_response(response, message, rate_info=None):
        lowered = message.lower()
        remaining = (rate_info or {}).get("remaining")
        return (
            response.status_code == 429
            or (response.status_code == 403 and str(remaining) == "0")
            or "secondary rate limit" in lowered
            or "rate limit exceeded" in lowered
            or "api rate limit exceeded" in lowered
        )

    def request_json(self, method, url, **kwargs):
        response = requests.request(
            method,
            url,
            headers=self.headers,
            timeout=self.timeout,
            **kwargs,
        )
        rate_info = self.rate_info_from_response(response)
        message = self.response_message(response)

        if self.is_rate_limited_response(response, message, rate_info):
            retry_after = positive_int(rate_info.get("retry_after"), 0, minimum=0)
            raise GitHubApiError(
                "GitHub rate limit reached; polling will slow down.",
                rate_limited=True,
                rate_info=rate_info,
                retry_after_seconds=retry_after or rate_info.get("reset_seconds"),
            )

        if response.status_code >= 400:
            detail = f": {message}" if message else ""
            raise GitHubApiError(
                f"GitHub error {response.status_code}{detail}",
                rate_info=rate_info,
            )

        return response.json(), rate_info

    def upload_image_error(self, error):
        if isinstance(error, GitHubApiError):
            message = str(error)
            lowered = message.lower()
            if "403" in message or "resource not accessible" in lowered:
                return GitHubApiError(
                    "Image upload failed. The GitHub token needs repository contents write access. "
                    "Use a classic PAT with the repo scope, or a fine-grained token with Contents: Read and write.",
                    rate_info=error.rate_info,
                    retry_after_seconds=error.retry_after_seconds,
                )
        return error

    def fetch_messages(self, issue_number=None):
        return self.request_json(
            "GET",
            self.issue_comments_url(issue_number),
            params={"per_page": 100},
        )

    def fetch_issues(self):
        data, rate_info = self.request_json(
            "GET",
            f"{self.repo_url}/issues",
            params={
                "state": "all",
                "sort": "updated",
                "direction": "desc",
                "per_page": 100,
            },
        )
        issues = [
            issue for issue in data
            if isinstance(issue, dict) and "pull_request" not in issue
        ]
        return issues, rate_info

    def fetch_current_user(self):
        data, rate_info = self.request_json("GET", self.user_url)
        return data.get("login", ""), rate_info

    def fetch_issue_title(self, issue_number=None):
        data, rate_info = self.request_json("GET", self.issue_api_url(issue_number))
        return data.get("title", ""), rate_info

    @staticmethod
    def clean_upload_folder(value):
        value = (value or DEFAULT_IMAGE_UPLOAD_FOLDER).strip().strip("/\\")
        parts = [
            re.sub(r"[^A-Za-z0-9._-]+", "-", part).strip(".-")
            for part in re.split(r"[/\\]+", value)
        ]
        parts = [part for part in parts if part]
        return "/".join(parts) or DEFAULT_IMAGE_UPLOAD_FOLDER

    @staticmethod
    def clean_upload_filename(filename):
        base_name = os.path.basename(filename or "image")
        name, extension = os.path.splitext(base_name)
        extension = extension.lower()
        if extension not in SUPPORTED_IMAGE_EXTENSIONS:
            extension = ".png"

        name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")[:60]
        return f"{name or 'image'}-{new_message_id()}{extension}"

    def upload_chat_image(self, filename, content_bytes):
        upload_name = self.clean_upload_filename(filename)
        month_folder = datetime.now().strftime("%Y-%m")
        repo_path = f"{self.image_upload_folder}/{month_folder}/{upload_name}"
        encoded_content = base64.b64encode(content_bytes).decode("ascii")
        try:
            data, rate_info = self.request_json(
                "PUT",
                f"{self.contents_url}/{quote(repo_path, safe='/')}",
                json={
                    "message": f"Add chat image {upload_name}",
                    "content": encoded_content,
                },
            )
        except Exception as error:
            raise self.upload_image_error(error) from error
        content = data.get("content") or {}
        raw_url = (
            f"https://raw.githubusercontent.com/{self.owner}/{self.repo}"
            f"/HEAD/{quote(repo_path, safe='/')}"
        )
        return {
            "path": repo_path,
            "url": raw_url,
            "download_url": content.get("download_url") or "",
            "html_url": content.get("html_url") or "",
        }, rate_info

    clean_reply_line = staticmethod(clean_reply_line)
    new_message_id = staticmethod(new_message_id)
    format_reply_block = staticmethod(format_reply_block)

    def send_message(self, text, reply=None, issue_number=None):
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        message_id = self.new_message_id()
        message = text.strip()
        reply_block = self.format_reply_block(reply)
        if reply_block:
            message = f"{reply_block}\n\n{message}"
        body = (
            f"**{self.display_name}** — `{now}`\n\n"
            f"<!-- chat-message-id: {message_id} -->\n\n"
            f"{message}"
        )
        data, rate_info = self.request_json(
            "POST",
            self.issue_comments_url(issue_number),
            json={"body": body},
        )
        return data, rate_info
