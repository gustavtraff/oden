"""
Signal-cli listener and message processor.

Main entry point that connects to signal-cli daemon and processes incoming messages.
Supports first-run setup wizard for initial configuration.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import logging
import sys
import time
import webbrowser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oden.tray import OdenTray

from oden import __version__
from oden.app_state import get_app_state
from oden.config import (
    DISPLAY_NAME,
    LOG_FILE,
    SIGNAL_CLI_HOST,
    SIGNAL_CLI_PORT,
    SIGNAL_NUMBER,
    UNMANAGED_SIGNAL_CLI,
    WEB_ENABLED,
    WEB_PORT,
    is_configured,
    reload_config,
)
from oden.log_buffer import get_log_buffer
from oden.log_utils import apply_log_level, read_log_level, write_log_level
from oden.processing import process_message
from oden.signal_manager import SignalManager, is_signal_cli_running

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    """Configure logging with console output, file output, and in-memory buffer.

    The log level is read from a persistent file next to the config database.
    If the file doesn't exist (first run / setup), DEBUG is used so that all
    setup activity is captured. After setup completes, the configured level
    is written to the file and applied via apply_log_level().
    """
    from logging.handlers import RotatingFileHandler
    from pathlib import Path

    level = read_log_level()

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler with rotation (5MB max, keep 3 backups)
    if LOG_FILE:
        try:
            log_path = Path(LOG_FILE).expanduser()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
            root_logger.info(f"Logging to file: {log_path}")
        except Exception as e:
            root_logger.warning(f"Could not set up file logging: {e}")

    # In-memory log buffer for web GUI
    log_buffer = get_log_buffer()
    log_buffer.setLevel(level)
    root_logger.addHandler(log_buffer)

    root_logger.info(f"Logging initialized at {logging.getLevelName(level)} level")


async def send_startup_message(writer: asyncio.StreamWriter, groups: list[dict] | None = None) -> None:
    """Sends a startup notification message based on STARTUP_MESSAGE config.

    Reads config values dynamically to support live reload after setup.

    Args:
        writer: The asyncio StreamWriter for sending messages.
        groups: List of group dictionaries from listGroups (required if mode is 'all').
    """
    # Import config values dynamically to get post-reload values
    from oden import config as cfg

    if cfg.STARTUP_MESSAGE == "off":
        logger.info("Startup message disabled (startup_message=off)")
        return

    now = datetime.datetime.now(cfg.TIMEZONE)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    message = f"🚀 Oden v{__version__} started\n📅 {timestamp}"

    try:
        if cfg.STARTUP_MESSAGE == "self":
            # Send to self only
            request_id = f"startup-{int(time.time())}"
            json_request = {
                "jsonrpc": "2.0",
                "method": "send",
                "params": {"account": cfg.SIGNAL_NUMBER, "recipient": [cfg.SIGNAL_NUMBER], "message": message},
                "id": request_id,
            }
            logger.info(f"Sending startup message to {cfg.SIGNAL_NUMBER}...")
            writer.write((json.dumps(json_request) + "\n").encode("utf-8"))
            await writer.drain()
            logger.info("Startup message sent to self.")

        elif cfg.STARTUP_MESSAGE == "all":
            if not groups:
                logger.warning("No groups available for startup message (startup_message=all)")
                return

            # Filter out ignored groups
            active_groups = [g for g in groups if g.get("name") not in cfg.IGNORED_GROUPS]
            if not active_groups:
                logger.info("No active groups to send startup message to (all groups ignored)")
                return

            logger.info(f"Sending startup message to {len(active_groups)} group(s)...")
            for group in active_groups:
                group_id = group.get("id")
                group_name = group.get("name", "Unknown")
                if not group_id:
                    continue

                request_id = f"startup-{group_id}-{int(time.time())}"
                json_request = {
                    "jsonrpc": "2.0",
                    "method": "send",
                    "params": {"account": cfg.SIGNAL_NUMBER, "groupId": group_id, "message": message},
                    "id": request_id,
                }
                writer.write((json.dumps(json_request) + "\n").encode("utf-8"))
                await writer.drain()
                logger.info(f"  • Sent to group: {group_name}")

            logger.info(f"Startup message sent to {len(active_groups)} group(s).")

    except Exception as e:
        logger.error(f"ERROR sending startup message: {e}")


async def log_groups(writer: asyncio.StreamWriter) -> list[dict]:
    """Fetches and logs all groups the account is a member of.

    Persists groups to the database so they survive restarts.
    Falls back to the database if the RPC call fails.

    Returns:
        List of group dictionaries (from signal-cli or DB).
    """
    from oden import config as cfg
    from oden.config import CONFIG_DB
    from oden.config_db import get_all_groups, upsert_groups_bulk

    app_state = get_app_state()

    try:
        response = await app_state.send_jsonrpc(
            "listGroups",
            params={"account": cfg.SIGNAL_NUMBER},
            timeout=10.0,
        )
        if response and "result" in response:
            groups = response["result"]
            # Cache groups in app_state for web GUI access
            app_state.update_groups(groups)

            # Persist to database
            count = upsert_groups_bulk(CONFIG_DB, groups)
            if count:
                logger.debug("Persisted %d groups to database", count)

            if not groups:
                logger.info("No groups found for this account.")
                return []

            logger.info(f"Account is member of {len(groups)} group(s):")
            for group in groups:
                group_name = group.get("name", "Unknown")
                is_ignored = group_name in cfg.IGNORED_GROUPS
                status = " (IGNORED)" if is_ignored else ""
                logger.info(f"  • {group_name}{status}")

            if cfg.IGNORED_GROUPS:
                ignored_count = sum(1 for g in groups if g.get("name") in cfg.IGNORED_GROUPS)
                logger.info(f"Ignored groups configured: {len(cfg.IGNORED_GROUPS)}, matched: {ignored_count}")

            return groups

        # RPC returned no result — fall back to DB
        logger.debug("listGroups returned no result, loading from database")

    except Exception as e:
        logger.warning(f"Could not fetch groups via RPC: {e} — loading from database")

    # Fallback: load from database
    db_groups = get_all_groups(CONFIG_DB)
    if db_groups:
        logger.info("Loaded %d group(s) from database (offline cache)", len(db_groups))
        app_state.update_groups(
            [
                {"id": g["id"], "name": g["name"], "members": [None] * g["memberCount"], "isMember": g["isMember"]}
                for g in db_groups
            ]
        )
    return db_groups


async def update_profile(writer: asyncio.StreamWriter, display_name: str | None) -> None:
    """Sends a JSON-RPC request to update the profile name."""
    from oden import config as cfg

    if not display_name:
        return

    request_id = f"update-profile-{int(time.time())}"
    json_request = {
        "jsonrpc": "2.0",
        "method": "updateProfile",
        "params": {"account": cfg.SIGNAL_NUMBER, "name": display_name},
        "id": request_id,
    }
    request_str = json.dumps(json_request) + "\n"

    try:
        logger.info(f"Attempting to update profile name to '{display_name}'...")
        writer.write(request_str.encode("utf-8"))
        await writer.drain()
        # Note: We are not waiting for a response here to avoid blocking.
        # The update is "fire and forget".
        logger.info("Profile name update request sent.")
    except Exception as e:
        logger.error(f"ERROR sending updateProfile request: {e}")


async def _reader_loop(reader: asyncio.StreamReader, app_state: object) -> None:
    """Read lines from the TCP socket and route them via the central dispatcher.

    Runs as a background task so that RPC Futures are resolved while
    other coroutines (log_groups, send_startup_message) are awaiting them.
    """
    while not reader.at_eof():
        line = await reader.readline()
        if not line:
            break

        message_str = line.decode("utf-8").strip()
        if not message_str:
            continue

        try:
            data = json.loads(message_str)
        except json.JSONDecodeError:
            logger.error(f"Received non-JSON message: {message_str}")
            continue

        app_state.dispatch_line(data)


async def subscribe_and_listen(host: str, port: int) -> None:
    """Connects to signal-cli via TCP socket, subscribes to messages, and processes them.

    Runs a single reader loop that dispatches all incoming lines:
    - RPC responses are routed to pending Futures (via app_state.dispatch_line)
    - Receive notifications are queued and processed here

    In multi-account daemon mode, messages include an 'account' field.
    Only messages for the active account (cfg.SIGNAL_NUMBER) are processed.
    """
    from oden import config as cfg

    logger.info(f"Connecting to signal-cli at {host}:{port}...")

    reader = None
    writer = None
    app_state = get_app_state()
    try:
        reader, writer = await asyncio.open_connection(host, port, limit=1024 * 1024 * 100)  # 100 MB limit
        logger.info("Connection successful. Waiting for messages...")

        # Share writer/reader with web server for sending commands
        app_state.writer = writer
        app_state.reader = reader

        # Start the reader/dispatcher loop as a background task so that
        # RPC responses are consumed while we issue startup calls.
        reader_task = asyncio.create_task(_reader_loop(reader, app_state))

        await update_profile(writer, DISPLAY_NAME)
        groups = await log_groups(writer)
        await send_startup_message(writer, groups)

        # Process notifications until the reader loop ends (connection closed)
        try:
            while not reader_task.done():
                try:
                    notification = await asyncio.wait_for(app_state.notification_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

                try:
                    params = notification.get("params")
                    if not params:
                        continue

                    # Multi-account mode: extract envelope from nested format
                    msg_data = params
                    if "result" in params and isinstance(params["result"], dict):
                        msg_data = params["result"]

                    # Filter: only process messages for the active account
                    msg_account = msg_data.get("account")
                    if msg_account and msg_account != cfg.SIGNAL_NUMBER:
                        logger.debug(f"Skipping message for non-active account: {msg_account}")
                        continue

                    await process_message(msg_data, reader, writer)
                except Exception as e:
                    logger.error(f"Could not process message.\n  Error: {repr(e)}")
        finally:
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader_task

    except ConnectionRefusedError as e:
        logger.error(f"Connection to signal-cli daemon failed: {e}")
        logger.error("Please ensure signal-cli is running in JSON-RPC mode with a TCP socket.")
        raise
    finally:
        # Clear shared state and cancel any pending RPC futures
        for future in app_state.rpc_pending.values():
            if not future.done():
                future.cancel()
        app_state.rpc_pending.clear()
        app_state.writer = None
        app_state.reader = None
        if writer:
            writer.close()
            await writer.wait_closed()
        logger.info("Connection closed.")


async def _run_lifecycle(
    host: str,
    port: int,
    signal_manager: SignalManager | None,
    tray: OdenTray | None,
) -> None:
    """Long-lived async lifecycle that keeps the web server running.

    The signal-cli listener is started/stopped independently via
    asyncio events stored on AppState.  The web server persists
    across stop/start cycles so the GUI is always reachable.
    """
    from oden.web_server import start_web_server

    app_state = get_app_state()
    loop = asyncio.get_running_loop()

    # Create lifecycle events and store on AppState
    stop_event = asyncio.Event()
    start_event = asyncio.Event()
    quit_event = asyncio.Event()

    app_state.loop = loop
    app_state.stop_event = stop_event
    app_state.start_event = start_event
    app_state.quit_event = quit_event
    app_state.signal_manager = signal_manager

    # Start the web server once — it stays alive for the entire lifetime
    web_runner = None
    if WEB_ENABLED:
        web_runner = await start_web_server(WEB_PORT)
        logger.info(f"Web GUI enabled on port {WEB_PORT}")

    listener_task: asyncio.Task | None = None

    try:
        while not quit_event.is_set():
            # Reset events for this cycle
            stop_event.clear()
            start_event.clear()

            # Start signal-cli if managed
            if signal_manager is not None:
                await asyncio.to_thread(signal_manager.start)
            elif not is_signal_cli_running(host, port):
                logger.error("signal-cli is not running. Please start it manually.")
                if tray is None:
                    break
                logger.info("Waiting for Start from tray menu...")
                if tray is not None:
                    tray.running = False
                # Wait for start or quit
                await _wait_for_event(start_event, quit_event)
                if quit_event.is_set():
                    break
                continue

            # Mark as running
            if tray is not None:
                tray.running = True

            # Run the listener as a cancellable task
            listener_task = asyncio.create_task(subscribe_and_listen(host, port))

            # Wait for either the listener to finish or a stop/quit signal
            stop_waiter = asyncio.create_task(stop_event.wait())
            done, pending = await asyncio.wait(
                {listener_task, stop_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel whichever is still pending
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

            # If the listener finished on its own (disconnect / error),
            # retrieve and log any exception
            if listener_task in done:
                exc = listener_task.exception() if not listener_task.cancelled() else None
                if exc is not None:
                    logger.error("Listener stopped with error: %s", exc)
                else:
                    logger.info("Listener disconnected.")
            listener_task = None

            # Stop signal-cli
            if tray is not None:
                tray.running = False
            if signal_manager is not None:
                await asyncio.to_thread(signal_manager.stop)

            # If no tray, exit after first run
            if tray is None:
                break

            if quit_event.is_set():
                break

            logger.info("Watcher stopped. Use tray menu to Start or Quit.")
            # Wait for user to click Start or Quit
            await _wait_for_event(start_event, quit_event)

    except asyncio.CancelledError:
        logger.info("Lifecycle cancelled.")
    finally:
        # Cancel listener if still running
        if listener_task is not None and not listener_task.done():
            listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await listener_task

        # Stop signal-cli
        if signal_manager is not None:
            await asyncio.to_thread(signal_manager.stop)

        # Clean up web server
        if web_runner is not None:
            await web_runner.cleanup()

        # Clear lifecycle state
        app_state.loop = None
        app_state.stop_event = None
        app_state.start_event = None
        app_state.quit_event = None
        app_state.signal_manager = None

        logger.info("Oden shut down.")


async def _wait_for_event(*events: asyncio.Event) -> None:
    """Wait until any of the given events is set."""
    waiters = [asyncio.create_task(e.wait()) for e in events]
    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for w in waiters:
            w.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await w


async def run_setup_mode(port: int) -> bool:
    """Run setup wizard and wait for configuration to complete.

    Args:
        port: Web server port.

    Returns:
        True if setup completed successfully.
    """
    from oden.web_server import run_setup_server

    logger.info("=" * 60)
    logger.info("🛡️  Välkommen till Oden!")
    logger.info("=" * 60)
    logger.info("")
    logger.info("Oden är inte konfigurerad ännu.")
    logger.info("En webbläsare öppnas nu för att guida dig genom setup.")
    logger.info("")
    logger.info(f"Om webbläsaren inte öppnas, gå till: http://127.0.0.1:{port}/setup")
    logger.info("")

    # Open browser after a short delay
    async def open_browser():
        await asyncio.sleep(1.0)
        url = f"http://127.0.0.1:{port}/setup"
        try:
            webbrowser.open(url)
            logger.info(f"Öppnade webbläsare: {url}")
        except Exception as e:
            logger.warning(f"Kunde inte öppna webbläsare: {e}")

    # Run web server and browser opener concurrently
    browser_task = asyncio.create_task(open_browser())
    result = await run_setup_server(port)
    browser_task.cancel()

    return result


def main() -> None:
    """Sets up the vault path, starts signal-cli, and begins listening.

    When pystray is available, a system tray icon is shown with Start/Stop,
    Open Web GUI, and Quit controls.  The watcher loop can be stopped and
    restarted from the tray without quitting the application.

    On macOS the tray event loop must run on the **main thread**, so
    ``tray.run()`` blocks main while the watcher logic runs in a
    background thread spawned by pystray's *setup* callback.
    """
    # Configure logging with console and buffer handlers
    configure_logging()

    logger.info(f"Starting Oden v{__version__}...")

    # Check if this is first run (not configured)
    _is_configured, _config_error = is_configured()
    if not _is_configured:
        logger.info(f"First run detected ({_config_error}) - starting setup wizard...")
        try:
            setup_complete = asyncio.run(run_setup_mode(WEB_PORT))
            if setup_complete:
                logger.info("Setup complete! Reloading configuration...")
                # Reload and get fresh config values
                new_config = reload_config()
                new_number = new_config["signal_number"]
                new_host = new_config["signal_cli_host"]
                new_port = new_config["signal_cli_port"]
                new_unmanaged = new_config["unmanaged_signal_cli"]
                # Persist and apply the configured log level
                log_level_str = new_config.get("log_level_str", "INFO")
                write_log_level(log_level_str)
                apply_log_level(new_config["log_level"])
            else:
                logger.error("Setup was not completed. Exiting.")
                sys.exit(1)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Setup cancelled by user.")
            sys.exit(0)
        except Exception as e:
            logger.exception(f"Error during setup: {e}")
            sys.exit(1)
    else:
        # Use existing config
        new_number = SIGNAL_NUMBER
        new_host = SIGNAL_CLI_HOST
        new_port = SIGNAL_CLI_PORT
        new_unmanaged = UNMANAGED_SIGNAL_CLI

    # Validate configuration
    if new_number == "+46XXXXXXXXX" or not new_number:
        logger.error("❌ Signal number not configured!")
        logger.error("Please run Oden again to complete setup.")
        sys.exit(1)

    # Set up system tray icon
    tray = _create_tray()
    app_state = get_app_state()
    app_state.tray = tray

    signal_manager = None if new_unmanaged else SignalManager(new_host, new_port)

    # --- Tray callbacks use AppState lifecycle helpers ---
    if tray is not None:
        tray.set_callbacks(
            on_start=app_state.request_start,
            on_stop=app_state.request_stop,
            on_quit=app_state.request_quit,
        )

    def _watcher_loop() -> None:
        """Run the async lifecycle (may be called from a background thread)."""
        try:
            asyncio.run(
                _run_lifecycle(
                    host=new_host,
                    port=new_port,
                    signal_manager=signal_manager,
                    tray=tray,
                )
            )
        except (KeyboardInterrupt, SystemExit):
            logger.info("Watcher loop stopped.")
        except Exception as e:
            logger.exception(f"An unexpected error occurred: {e}")
        finally:
            if tray is not None:
                tray.stop()

    if tray is not None:
        # tray.run() blocks main thread (required for macOS NSApp loop).
        # The watcher loop runs in pystray's setup-callback thread.
        tray.run(on_ready=_watcher_loop)
    else:
        # No tray — run the watcher directly on the main thread.
        _watcher_loop()

    sys.exit(0)


def _create_tray() -> OdenTray | None:
    """Create the system tray icon, or return *None* if pystray is unavailable."""
    try:
        from oden.tray import OdenTray

        tray = OdenTray(version=__version__, web_port=WEB_PORT)
        if tray.setup():
            return tray
        return None
    except Exception as e:
        logger.debug("System tray not available: %s", e)
        return None


if __name__ == "__main__":
    main()
