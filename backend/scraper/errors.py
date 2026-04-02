"""Scraper-specific exceptions."""

from __future__ import annotations

from dataclasses import dataclass, field


class SessionExpiredError(Exception):
    """Raised when a platform's authenticated session has expired or hit a login wall."""

    def __init__(self, platform: str) -> None:
        self.platform = platform
        super().__init__(f"Session expired for {platform}")


@dataclass
class PostCandidate:
    """A scraped post candidate before it is written to the database."""

    source_url: str
    creator: str
    engagement: int
    screenshot_data: bytes = field(default_factory=bytes)
    from_creator: bool = False  # True when scraped from a followed creator profile
