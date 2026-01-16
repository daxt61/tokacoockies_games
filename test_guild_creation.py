
import pytest
from playwright.sync_api import sync_playwright, expect
import subprocess
import time
import os
import socket

@pytest.fixture(scope="session")
def server():
    # Start the server in a separate process
    server_process = subprocess.Popen(["python", "server.py"])

    # Wait for the server to be ready by checking the port
    for _ in range(10): # Try for 5 seconds
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('localhost', 5000)) == 0:
                break
        time.sleep(0.5)
    else:
        raise RuntimeError("Server did not start in time.")

    yield
    # Teardown: stop the server
    server_process.terminate()
    server_process.wait()

def test_guild_creation_and_ui_update(server):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:5000")

        # Authenticate
        page.fill("#auth-user", "testuser")
        page.fill("#auth-pass", "testpass")
        page.click("text=S'INSCRIRE")
        page.click("text=JOUER")
        page.wait_for_selector("#auth-overlay", state="hidden")

        # Go to the guild view
        page.locator(".nav-item", has_text="Guildes").click()

        # Check that the no-guild-state is visible
        assert page.is_visible("#no-guild-state")
        assert not page.is_visible("#has-guild-state")

        # Create a guild
        page.fill("#guild-name-input", "Test Guild")
        page.click("text=FONDER UNE GUILDE")

        # Wait for the UI to update and check that the has-guild-state is visible
        page.wait_for_selector("#has-guild-state", state="visible")

        # Verify the guild name is displayed correctly
        expect(page.locator("#guild-name-display")).to_have_text("Test Guild")

        # Take a screenshot
        page.screenshot(path="guild_creation_test.png")

        browser.close()
