"""Real DuckDB + HTTP + Chromium smoke test. Run explicitly, not under pytest."""
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from playwright.sync_api import sync_playwright
from xvi.data import ingest
from xvi.demo import generate


def main():
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        trades,metadata=generate(root)
        db=root/'demo.duckdb'
        ingest([str(trades)],db,str(metadata),demo=True,memory='1GB',threads=2)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port=sock.getsockname()[1]
        process=subprocess.Popen([sys.executable,'-m','xvi','serve','--db',str(db),'--cache',str(root/'cache'),'--port',str(port)])
        try:
            url=f'http://127.0.0.1:{port}'
            for _ in range(100):
                try:
                    with urlopen(url+'/api/status',timeout=1) as response:
                        assert response.status==200
                    break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError('Dashboard server exited')
                    time.sleep(.2)
            else:
                raise RuntimeError('Dashboard server did not become ready')
            with sync_playwright() as pw:
                browser=pw.chromium.launch()
                page=browser.new_page(viewport={'width':1512,'height':1100})
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(url)
                page.wait_for_function("document.querySelectorAll('#trade-body tr').length===50",timeout=120000)
                page.locator('#chart-loading').wait_for(state='hidden')
                assert page.locator('#demo-banner').is_visible()
                Path('test-results').mkdir(exist_ok=True)
                page.screenshot(path='test-results/desktop.png',full_page=True)
                page.locator('[data-range="hour"]').click()
                page.wait_for_function("document.getElementById('resolution-badge').textContent.includes('RAW')")
                page.locator('#chart-loading').wait_for(state='hidden')
                page.locator('#trade-body tr').first.click()
                assert page.locator('#trade-dialog').is_visible()
                page.keyboard.press('Escape')
                with page.expect_download() as download:
                    page.locator('#export').click()
                assert download.value.suggested_filename.endswith('-observed-fills.csv')
                page.locator('[data-range="all"]').click()
                page.locator('#chart-loading').wait_for(state='hidden')
                page.locator('#comparison-tab').click()
                page.wait_for_function("document.querySelectorAll('.event-card[data-ready=true]').length===3",timeout=120000)
                page.screenshot(path='test-results/events.png',full_page=True)
                page.locator('#price-tab').click()
                page.locator('#chart-loading').wait_for(state='hidden')
                page.locator('#theme').click()
                page.screenshot(path='test-results/dark.png',full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                page.wait_for_timeout(200)
                assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
                page.screenshot(path='test-results/mobile.png',full_page=True)
                page.locator('#theme').click()
                assert not errors,errors
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__=='__main__':
    main()
