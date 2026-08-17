import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import server


class PidSaturationTests(unittest.IsolatedAsyncioTestCase):
    def test_pid_threshold_detects_near_saturation(self):
        self.assertTrue(server.pid_limit_is_saturated({"pids_current": "994", "pids_max": "1000"}))
        self.assertFalse(server.pid_limit_is_saturated({"pids_current": "899", "pids_max": "1000"}))
        self.assertFalse(server.pid_limit_is_saturated({"pids_current": "994", "pids_max": "max"}))

    async def test_saturated_cycle_skips_driver_and_unlocks_refresh(self):
        deck = server.XDeckApp()
        deck.subscriptions = {"one": {"query": "railway"}}
        deck.broadcast = AsyncMock()

        with (
            patch.object(server, "process_limit_summary", return_value={"pids_current": "994", "pids_max": "1000"}),
            patch.object(server, "chromium_process_summary", return_value={"chromium_process_count": 0}),
            patch.object(server, "cleanup_orphaned_chromium_processes", return_value=0),
            patch.object(server, "async_playwright") as playwright,
            patch.object(server, "is_critical_window_now", return_value=True),
        ):
            deck.schedule_refresh_all(source="auto")
            task = deck._refresh_task
            await task

        playwright.assert_not_called()
        self.assertIsNone(deck._refresh_task)
        self.assertIsNone(deck._refresh_started_at)

    async def test_launch_failure_is_one_attempt_for_whole_cycle(self):
        deck = server.XDeckApp()
        deck.subscriptions = {
            str(index): {"query": f"query {index}"} for index in range(5)
        }
        deck.broadcast = AsyncMock()
        deck.bm.fetch_many = AsyncMock(side_effect=RuntimeError("Falha ao iniciar navegador temporário para esta coleta"))

        await deck.refresh_all(source="auto")

        deck.bm.fetch_many.assert_awaited_once()
        self.assertIsNone(deck._refresh_task)


if __name__ == "__main__":
    unittest.main()
