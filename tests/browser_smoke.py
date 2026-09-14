"""Real DuckDB + HTTP + Chromium smoke test, with the production CSP intact."""
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from playwright.sync_api import sync_playwright, expect
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
        output=Path('test-results')
        output.mkdir(exist_ok=True)
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
                try:
                    response=page.goto(url)
                    assert "script-src 'self'" in response.headers['content-security-policy']
                    # Locator assertions avoid eval-based polling rejected by the CSP.
                    expect(page.locator('#trade-body tr')).to_have_count(50,timeout=120000)
                    expect(page.locator('#chart-loading')).to_be_hidden()
                    expect(page.locator('#demo-banner')).to_be_visible()
                    page.screenshot(path=str(output/'desktop.png'),full_page=True)
                    page.locator('[data-range="hour"]').click()
                    expect(page.locator('#resolution-badge')).to_contain_text('RAW')
                    expect(page.locator('#chart-loading')).to_be_hidden()
                    page.locator('#trade-body tr').first.click()
                    expect(page.locator('#trade-dialog')).to_be_visible()
                    page.keyboard.press('Escape')
                    with page.expect_download() as download:
                        page.locator('#export').click()
                    assert download.value.suggested_filename.endswith('-observed-fills.csv')
                    page.locator('[data-range="all"]').click()
                    expect(page.locator('#chart-loading')).to_be_hidden()
                    page.locator('#comparison-tab').click()
                    expect(page.locator('.event-card[data-ready=true]')).to_have_count(3,timeout=120000)
                    page.screenshot(path=str(output/'events.png'),full_page=True)
                    page.locator('#price-tab').click()
                    expect(page.locator('#chart-loading')).to_be_hidden()
                    page.locator('#theme').click()
                    expect(page.locator('html')).to_have_attribute('data-theme','dark')
                    page.screenshot(path=str(output/'dark.png'),full_page=True)
                    page.set_viewport_size({'width':390,'height':844})
                    page.wait_for_timeout(200)
                    assert page.locator('html').evaluate('(el) => el.scrollWidth')<=390
                    page.screenshot(path=str(output/'mobile.png'),full_page=True)
                    page.locator('#theme').click()
                    assert not errors,errors
                    print('Browser smoke passed: real ingestion/API, raw zoom, inspector, CSV, event view, theme and mobile layout.')
                except BaseException:
                    page.screenshot(path=str(output/'failure.png'),full_page=True)
                    (output/'browser-errors.txt').write_text('\n'.join(errors))
                    raise
                finally:
                    browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__=='__main__':
    main()
