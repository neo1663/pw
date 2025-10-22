from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, Optional

from pathlib import Path

from .client import BlueskyClient, BlueskyError, DirectMessageNotSupported
from .config import AccountConfig, Config, TargetConfig, load_config
from .storage import AccountState, StateStore, TargetState

logger = logging.getLogger(__name__)


class SafeDict(dict):
    """Dictionary that returns an empty string for missing keys when formatting."""

    def __missing__(self, key: str) -> str:
        return ""


@dataclass
class AutomationContext:
    config: Config
    store: StateStore
    dry_run: bool = False


class BlueskyAutomator:
    """Coordinates automation actions across multiple accounts."""

    def __init__(self, context: AutomationContext) -> None:
        self.context = context

    def run(self) -> None:
        for account in self.context.config.accounts:
            try:
                self._run_for_account(account)
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Unexpected error for %s: %s", account.handle, exc)

    # ------------------------------------------------------------------
    def _run_for_account(self, account: AccountConfig) -> None:
        logger.info("Processing account %s", account.handle)
        state = self.context.store.load(account.handle)

        if self.context.dry_run:
            logger.info("Dry-run mode: configuration for %s validated", account.handle)
            return

        with BlueskyClient(
            account.handle,
            account.app_password,
            service=account.service,
            proxy=account.proxy,
        ) as client:
            try:
                profile = client.login()
            except BlueskyError as exc:
                logger.error("Failed to authenticate %s: %s", account.handle, exc)
                return

            logger.info("Authenticated as %s (%s)", profile.handle, profile.did)

            for target in account.follow_targets:
                self._engage_target(client, account, target, state)

            if account.dm.enabled and account.dm.message.strip():
                self._message_new_followers(client, account, state)

            self.context.store.save(account.handle, state)

    # ------------------------------------------------------------------
    def _engage_target(
        self,
        client: BlueskyClient,
        account: AccountConfig,
        target: TargetConfig,
        state: AccountState,
    ) -> None:
        logger.info("Processing target %s for %s", target.handle, account.handle)
        try:
            target_profile = client.get_profile(target.handle)
        except BlueskyError as exc:
            logger.error("Failed to resolve target %s: %s", target.handle, exc)
            return

        target_state = state.target(target.handle)

        followed_count = 0
        liked_count = 0
        follow_limit = target.follow_limit
        like_limit = target.like_limit

        for follower in client.iterate_followers(target_profile.did):
            follower_did = follower.get("did")
            if not isinstance(follower_did, str):
                continue
            if follower_did == client.did:
                continue
            if follower_did in target_state.followed:
                continue

            follower_handle = follower.get("handle") or follower_did
            logger.info(
                "Following %s (target %s)",
                follower_handle,
                target.handle,
            )

            try:
                client.follow(follower_did)
            except BlueskyError as exc:
                logger.warning("Could not follow %s: %s", follower_handle, exc)
                target_state.followed.add(follower_did)
                continue

            target_state.followed.add(follower_did)
            followed_count += 1

            delay = self._effective_delay(
                account.follow_delay_seconds, self.context.config.default_follow_delay_seconds
            )
            if delay > 0:
                time.sleep(delay)

            if target.like_latest_post and (like_limit is None or liked_count < like_limit):
                if self._like_latest_post(client, follower, target_state):
                    liked_count += 1
                    like_delay = self._effective_delay(
                        account.like_delay_seconds, self.context.config.default_like_delay_seconds
                    )
                    if like_delay > 0:
                        time.sleep(like_delay)

            if follow_limit is not None and followed_count >= follow_limit:
                logger.info(
                    "Reached follow limit (%s) for target %s", follow_limit, target.handle
                )
                break

        logger.info(
            "Finished target %s: %s follows, %s likes",
            target.handle,
            followed_count,
            liked_count,
        )

    def _like_latest_post(
        self,
        client: BlueskyClient,
        follower: Dict[str, object],
        target_state: TargetState,
    ) -> bool:
        follower_did = follower.get("did")
        if not isinstance(follower_did, str):
            return False

        try:
            feed = client.get_author_feed(follower_did, limit=1)
        except BlueskyError as exc:
            logger.debug("Failed to fetch feed for %s: %s", follower_did, exc)
            return False

        posts = feed.get("feed", []) if isinstance(feed, dict) else []
        if not isinstance(posts, list):
            return False

        for item in posts:
            post = item.get("post") if isinstance(item, dict) else None
            if not isinstance(post, dict):
                continue
            uri = post.get("uri")
            cid = post.get("cid")
            if not isinstance(uri, str) or not isinstance(cid, str):
                continue
            if uri in target_state.liked_posts:
                return False
            try:
                client.like(uri, cid)
            except BlueskyError as exc:
                logger.debug("Failed to like %s: %s", uri, exc)
                return False
            target_state.liked_posts.add(uri)
            logger.info("Liked latest post %s", uri)
            return True
        return False

    # ------------------------------------------------------------------
    def _message_new_followers(
        self,
        client: BlueskyClient,
        account: AccountConfig,
        state: AccountState,
    ) -> None:
        dm_config = account.dm
        logger.info("Checking for new followers to message for %s", account.handle)

        sent = 0
        cursor: Optional[str] = None
        cooldown = timedelta(hours=dm_config.cooldown_hours) if dm_config.cooldown_hours else None

        while True:
            try:
                response = client.list_own_followers(limit=account.new_followers_page_size, cursor=cursor)
            except BlueskyError as exc:
                logger.error("Failed to fetch followers for %s: %s", account.handle, exc)
                return

            followers = response.get("followers", []) if isinstance(response, dict) else []
            if not isinstance(followers, list):
                followers = []

            for follower in followers:
                follower_did = follower.get("did") if isinstance(follower, dict) else None
                if not isinstance(follower_did, str):
                    continue

                state.known_followers.add(follower_did)

                if not self._should_send_dm(follower_did, state, cooldown):
                    continue

                if dm_config.limit_per_run is not None and sent >= dm_config.limit_per_run:
                    logger.info("Reached DM limit (%s) for %s", dm_config.limit_per_run, account.handle)
                    return

                message = self._render_message(dm_config.message, follower)

                try:
                    convo_id = client.create_or_get_conversation(follower_did)
                    client.send_direct_message(convo_id, message)
                except DirectMessageNotSupported as exc:
                    logger.warning("Direct messages not supported: %s", exc)
                    return
                except BlueskyError as exc:
                    logger.warning("Failed to send DM to %s: %s", follower_did, exc)
                    continue

                state.dm_history[follower_did] = datetime.now(timezone.utc).isoformat()
                sent += 1
                logger.info("Sent DM to %s", follower.get("handle") or follower_did)

                delay = self._effective_delay(
                    account.follow_delay_seconds, self.context.config.default_follow_delay_seconds
                )
                if delay > 0:
                    time.sleep(delay)

            cursor = response.get("cursor") if isinstance(response, dict) else None
            if not cursor:
                break

    # ------------------------------------------------------------------
    def _should_send_dm(
        self,
        did: str,
        state: AccountState,
        cooldown: Optional[timedelta],
    ) -> bool:
        if did not in state.dm_history:
            return True
        if not cooldown:
            return False

        last_sent_str = state.dm_history.get(did)
        if not last_sent_str:
            return True

        try:
            last_sent = datetime.fromisoformat(last_sent_str)
        except ValueError:
            return True

        if last_sent.tzinfo is None:
            last_sent = last_sent.replace(tzinfo=timezone.utc)

        return datetime.now(timezone.utc) - last_sent >= cooldown

    def _render_message(self, template: str, follower: Dict[str, object]) -> str:
        placeholders = SafeDict(
            handle=follower.get("handle", ""),
            displayName=follower.get("displayName", ""),
            did=follower.get("did", ""),
        )
        return template.format_map(placeholders)

    def _effective_delay(self, value: Optional[float], fallback: float) -> float:
        return fallback if value is None else float(value)


def build_automator(config: Config, *, dry_run: bool = False) -> BlueskyAutomator:
    context = AutomationContext(config=config, store=StateStore(config.storage_dir), dry_run=dry_run)
    return BlueskyAutomator(context)


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Automate Bluesky outreach workflows.")
    parser.add_argument("--config", "-c", type=Path, required=True, help="Path to YAML configuration file.")
    parser.add_argument("--dry-run", action="store_true", help="Validate configuration without hitting the API.")
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (DEBUG, INFO, WARNING, ERROR).",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        config = load_config(Path(args.config))
    except Exception as exc:  # pragma: no cover - CLI convenience
        logger.error("Failed to load configuration: %s", exc)
        return 1

    automator = build_automator(config, dry_run=args.dry_run)
    automator.run()

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
