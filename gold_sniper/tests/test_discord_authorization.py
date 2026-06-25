"""Tests cibles garde Discord fail-closed P0-G."""
from __future__ import annotations

import types
import unittest

from utils.discord_commands import MUTATING_COMMANDS, discord_command_authorization_failure


def cfg(user: int = 0, guild: int = 0, channel: int = 0):
    return types.SimpleNamespace(
        DISCORD_USER_ID=user,
        DISCORD_GUILD_ID=guild,
        DISCORD_COMMANDS_CHANNEL=channel,
    )


class TestDiscordAuthorization(unittest.TestCase):
    def test_empty_config_refuses_mutating_command(self) -> None:
        failure = discord_command_authorization_failure(
            "pause",
            user_id=1,
            guild_id=2,
            channel_id=3,
            config_module=cfg(),
        )
        self.assertEqual(failure, "discord_mutating_config_incomplete")

    def test_wrong_user_guild_channel_refused(self) -> None:
        config = cfg(user=10, guild=20, channel=30)
        self.assertEqual(
            discord_command_authorization_failure("risk", user_id=11, guild_id=20, channel_id=30, config_module=config),
            "discord_wrong_user",
        )
        self.assertEqual(
            discord_command_authorization_failure("risk", user_id=10, guild_id=21, channel_id=30, config_module=config),
            "discord_wrong_guild",
        )
        self.assertEqual(
            discord_command_authorization_failure("risk", user_id=10, guild_id=20, channel_id=31, config_module=config),
            "discord_wrong_channel",
        )

    def test_valid_user_guild_channel_allows_mutating_command(self) -> None:
        failure = discord_command_authorization_failure(
            "resume",
            user_id=10,
            guild_id=20,
            channel_id=30,
            config_module=cfg(user=10, guild=20, channel=30),
        )
        self.assertIsNone(failure)

    def test_read_only_can_pass_without_config(self) -> None:
        failure = discord_command_authorization_failure(
            "status",
            user_id=0,
            guild_id=0,
            channel_id=0,
            config_module=cfg(),
        )
        self.assertIsNone(failure)

    def test_no_mutating_command_fails_open_with_empty_config(self) -> None:
        for command in MUTATING_COMMANDS:
            with self.subTest(command=command):
                failure = discord_command_authorization_failure(
                    command,
                    user_id=1,
                    guild_id=2,
                    channel_id=3,
                    config_module=cfg(),
                )
                self.assertEqual(failure, "discord_mutating_config_incomplete")


if __name__ == "__main__":
    unittest.main()
