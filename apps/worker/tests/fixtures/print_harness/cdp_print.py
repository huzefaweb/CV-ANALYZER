"""Minimal Chrome DevTools Protocol print-to-PDF driver for the AF-12 print harness.

Drives the pinned, documented browser (Story 1.5d) headlessly to render a
static fixture HTML file to PDF exactly as its print preview would, with
browser-injected headers/footers disabled (the "clean export" a Recruiter
would choose). No Playwright/Selenium: this reuses `httpx` (pinned) and
`websockets` (pinned as a dev dependency by this story) directly against the
already-installed browser executable, per the ponytail "already-installed
dependency" rung validated live during story creation.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import pathlib
import shutil
import socket
import subprocess
import tempfile

import httpx
import websockets

DEFAULT_CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def resolve_chrome_path() -> str:
    """Resolve the documented browser's executable path.

    Overridable via PRINT_HARNESS_CHROME_PATH so the harness never silently
    fabricates a result when the pinned browser is absent from a machine.
    """
    return os.environ.get("PRINT_HARNESS_CHROME_PATH", DEFAULT_CHROME_PATH)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _print_via_cdp(port: int, file_url: str) -> bytes:
    async with httpx.AsyncClient() as client:
        # Create a blank target first. Passing file_url straight to /json/new
        # would navigate it immediately, before Page.enable is listening —
        # the later explicit Page.navigate to that same URL then never fires
        # a fresh Page.loadEventFired (identical-URL navigations are a
        # same-document no-op), hanging the harness forever. Blank-then-
        # navigate avoids the race.
        resp = await client.put(f"http://127.0.0.1:{port}/json/new?about:blank")
        resp.raise_for_status()
        target = resp.json()

    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=None) as ws:
        msg_id = 0

        async def send(method: str, params: dict | None = None) -> int:
            nonlocal msg_id
            msg_id += 1
            await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            return msg_id

        # ponytail: a message can in principle arrive out of the order this
        # loop expects (e.g. Page.loadEventFired before the Page.navigate
        # command response) and get silently skipped by whichever waiter
        # wasn't looking for it. Not observed in practice, and bounded by the
        # 30s outer timeout in render_to_pdf (a stuck wait now fails loudly
        # instead of hanging forever) — upgrade to a shared inbox/dispatcher
        # if that timeout ever starts firing spuriously.
        def _check_cdp_result(data: dict, what: str) -> dict:
            if "error" in data:
                raise RuntimeError(f"CDP {what} failed: {data['error']}")
            return data

        async def recv_until(target_id: int, what: str) -> dict:
            while True:
                data = json.loads(await ws.recv())
                if data.get("id") == target_id:
                    return _check_cdp_result(data, what)

        await send("Page.enable")
        nav_id = await send("Page.navigate", {"url": file_url})
        await recv_until(nav_id, "Page.navigate")

        async def wait_for_load() -> None:
            while True:
                data = json.loads(await ws.recv())
                if data.get("method") == "Page.loadEventFired":
                    return

        await asyncio.wait_for(wait_for_load(), timeout=15)

        print_id = await send(
            "Page.printToPDF",
            {
                "printBackground": True,
                "preferCSSPageSize": True,
                "displayHeaderFooter": False,
                "marginTop": 0,
                "marginBottom": 0,
                "marginLeft": 0,
                "marginRight": 0,
            },
        )
        result = await recv_until(print_id, "Page.printToPDF")
        return base64.b64decode(result["result"]["data"])


def render_to_pdf(file_path: str) -> bytes:
    """Render a static fixture HTML file to PDF via the pinned, documented browser.

    Launches a fresh, disposable Chrome profile per call and tears it down
    unconditionally, so repeated harness runs never leak processes or state
    between fixtures.
    """
    chrome_path = resolve_chrome_path()
    if not os.path.exists(chrome_path):
        raise FileNotFoundError(
            f"Documented browser not found at {chrome_path!r}. "
            "Set PRINT_HARNESS_CHROME_PATH to the pinned Chrome executable."
        )

    # pathlib builds a correctly percent-encoded file:// URL (spaces, '#',
    # non-ASCII, etc.) — a hand-rolled "file:///" + replace("\\","/") would
    # silently mis-navigate on any fixture path containing such characters.
    file_url = pathlib.Path(file_path).resolve().as_uri()

    last_error: Exception | None = None
    for attempt in range(2):  # ephemeral-port allocation is TOCTOU; one retry covers a rare collision
        port = _free_port()
        profile_dir = tempfile.mkdtemp(prefix="print-harness-profile-")
        proc: subprocess.Popen | None = None
        try:
            proc = subprocess.Popen(
                [
                    chrome_path,
                    "--headless=new",
                    "--disable-gpu",
                    f"--remote-debugging-port={port}",
                    f"--user-data-dir={profile_dir}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Poll the DevTools HTTP endpoint until Chrome is ready to accept targets.
            async def wait_ready() -> None:
                async with httpx.AsyncClient() as client:
                    for _ in range(50):
                        try:
                            r = await client.get(f"http://127.0.0.1:{port}/json/version", timeout=0.2)
                            if r.status_code == 200:
                                return
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(0.1)
                raise TimeoutError(f"Chrome DevTools endpoint on port {port} did not become ready within 5s")

            async def run() -> bytes:
                await wait_ready()
                return await _print_via_cdp(port, file_url)

            # Defense in depth on top of the blank-then-navigate fix above: never
            # let a CDP protocol hiccup hang the harness (and the Chrome process
            # it owns) indefinitely.
            return asyncio.run(asyncio.wait_for(run(), timeout=30))
        except TimeoutError as exc:
            last_error = exc
            continue  # port collision or a genuinely slow Chrome start; try once more on a fresh port
        finally:
            if proc is not None and proc.poll() is None:
                _kill_process_tree(proc.pid)
            shutil.rmtree(profile_dir, ignore_errors=True)

    raise TimeoutError(f"Chrome DevTools endpoint did not become ready after 2 attempts") from last_error


def _kill_process_tree(pid: int) -> None:
    """Terminate exactly this launched Chrome instance and its child processes.

    `--headless=new` spawns renderer/GPU child processes that outlive a plain
    `Popen.terminate()` on Windows (it only signals the direct process),
    leaving orphaned `chrome.exe` processes behind. Scoped to this one PID's
    tree via `/PID` — never a blanket `/IM chrome.exe` kill, which would tear
    down every Chrome window on the machine, including a user's own browser.
    """
    result = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Not fatal for the render that already succeeded/failed on its own
        # terms, but surfaced rather than silently swallowed — an orphaned
        # Chrome process should be visible somewhere.
        print(f"print_harness: taskkill for pid {pid} exited {result.returncode}: {result.stderr.strip()}")


def resolve_chrome_version(chrome_path: str) -> str:
    """Read the documented browser's exact installed version via PowerShell.

    Re-read at run time (never hardcoded) so evidence always reflects the
    browser actually exercised by the harness.
    """
    # Single-quoted PowerShell string: escape embedded ' by doubling it,
    # per PowerShell's own quoting rule, rather than trusting chrome_path
    # (an OS-controlled but still externally-influenced value via
    # PRINT_HARNESS_CHROME_PATH) to never contain one.
    escaped_path = chrome_path.replace("'", "''")
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-Item '{escaped_path}').VersionInfo.ProductVersion",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return result.stdout.strip()
