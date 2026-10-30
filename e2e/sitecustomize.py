"""E2E-only publisher replacement enabled by the stack launcher."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path


if os.environ.get("E2E_MOCK_PUBLISHER") == "1":
    from publisher import registry
    from publisher.base import PublishResult

    _build_publisher_from_account = registry.build_publisher_from_account


    class _E2EPublisher:
        def __init__(self, account_name: str) -> None:
            self.account_name = account_name

        def publish(self, draft_id: int, title: str, content: str, **_kwargs) -> PublishResult:
            log_dir = Path(os.environ.get("E2E_LOG_DIR", "/tmp/paper-pulse-e2e-logs")).expanduser().resolve()
            account_dir = re.sub(r"[^A-Za-z0-9._-]+", "-", self.account_name).strip("-") or "account"
            output_dir = log_dir / "published" / account_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"draft-{draft_id}-{time.time_ns()}.md"
            output_path.write_text(f"# {title}\n\n{content}\n", encoding="utf-8")
            return PublishResult(
                success=True,
                channel="csdn",
                url=output_path.as_uri(),
                raw={"path": str(output_path)},
            )


    def _build_e2e_publisher_from_account(
        account: dict,
        workspace: str = ".",
        config_dir: str = "config",
    ):
        account_name = str(account.get("account_name") or "")
        if account.get("channel") == "csdn" and account_name.startswith("e2e-"):
            return _E2EPublisher(account_name)
        return _build_publisher_from_account(account, workspace=workspace, config_dir=config_dir)


    registry.build_publisher_from_account = _build_e2e_publisher_from_account
