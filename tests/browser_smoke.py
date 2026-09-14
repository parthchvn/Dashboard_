"""Real DuckDB + HTTP + Chromium smoke test, with the production CSP intact."""
import csv
import os
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
                browser=pw.chromium.launch(executable_path=os.environ.get('XVI_BROWSER_EXECUTABLE') or None)
                page=browser.new_page(viewport={'width':1512,'height':1100},timezone_id='America/New_York')
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
                    # Both date cells are directly editable; no modal or preset row.
                    expect(page.locator('#range-start')).to_be_visible()
                    expect(page.locator('#range-end')).to_be_visible()
                    expect(page.locator('#range-start')).to_have_attribute('type','date')
                    expect(page.locator('#range-dialog')).to_have_count(0)
                    page.locator('#range-start').fill('2025-03-01')
                    page.locator('#range-end').fill('2025-03-01')
                    with page.expect_response(lambda r:'/series?' in r.url and 'start=' in r.url) as day_response:
                        page.locator('#apply-filters').click()
                    day=day_response.value.json()
                    assert day['start']==1740787200 and day['end']==1740873600
                    assert day['stats']['fill_count']>0
                    expect(page.locator('#resolution-badge')).to_contain_text('RAW')
                    expect(page.locator('#chart-loading')).to_be_hidden()
                    page.locator('#trade-body tr').first.locator('td').first.click()
                    expect(page.locator('#trade-dialog')).to_be_visible()
                    page.keyboard.press('Escape')
                    with page.expect_download() as download:
                        page.locator('#export').click()
                    assert download.value.suggested_filename.endswith('-observed-fills.csv')
                    # Invalid dates do not silently change the active view.
                    old_count=page.locator('#metric-fills').inner_text()
                    page.locator('#range-start').fill('2025-03-02')
                    page.locator('#apply-filters').click()
                    expect(page.locator('#filter-error')).to_contain_text('on or after')
                    expect(page.locator('#metric-fills')).to_have_text(old_count)
                    page.locator('#range-start').fill('2025-03-01')
                    page.locator('#wallet-ids').fill('0x123')
                    page.locator('#apply-filters').click()
                    expect(page.locator('#filter-error')).to_contain_text('full wallet')
                    page.locator('#wallet-ids').fill('')

                    # Click a wallet straight from the tape; role defaults to that cell.
                    wallet_button=page.locator('.wallet-button[data-role="taker"]').first
                    wallet=wallet_button.get_attribute('data-wallet').lower()
                    expected=[r for r in day['rows'] if r['taker'].lower()==wallet]
                    with page.expect_response(lambda r:'/series?' in r.url and 'wallets=' in r.url and 'start=' in r.url) as filtered_response:
                        wallet_button.click()
                    filtered=filtered_response.value.json()
                    expect(page.locator('#chart-loading')).to_be_hidden()
                    expect(page.locator('#wallet-ids')).to_have_value(wallet)
                    expect(page.locator('#wallet-role')).to_have_value('taker')
                    expect(page.locator('#metric-fills')).to_have_text(str(len(expected)))
                    expect(page.locator('#show-reviews')).to_be_disabled()
                    assert filtered['filters']['wallet_role']=='taker'
                    assert len(filtered['rows'])==len(expected)>0
                    with page.expect_download() as selected_download:
                        page.locator('#export').click()
                    exported=list(csv.DictReader(Path(selected_download.value.path()).read_text().splitlines()))
                    assert len(exported)==len(expected)
                    assert all(r['taker'].lower()==wallet for r in exported)
                    page.screenshot(path=str(output/'wallet-filter.png'),full_page=True)

                    # Address OR + deduplication + case folding, with both roles.
                    maker=day['rows'][0]['maker'].lower()
                    chosen={wallet,maker}
                    page.locator('#wallet-ids').fill(wallet.upper()+', '+maker+'\n'+wallet)
                    page.locator('#wallet-role').select_option('either')
                    with page.expect_response(lambda r:'/series?' in r.url and 'start=' in r.url) as combined_response:
                        page.locator('#apply-filters').click()
                    combined=combined_response.value.json()
                    expect(page.locator('#chart-loading')).to_be_hidden()
                    expected=[r for r in day['rows'] if r['maker'].lower() in chosen or r['taker'].lower() in chosen]
                    assert combined['stats']['fill_count']==len(expected)
                    assert set(combined['filters']['wallets'])==chosen
                    expect(page.locator('#filter-badge')).to_have_text('2 WALLETS')
                    # Copied views retain wallet and UTC date filters after a reload.
                    saved_url=page.url
                    page.reload()
                    expect(page.locator('#filter-badge')).to_have_text('2 WALLETS')
                    expect(page.locator('#chart-loading')).to_be_hidden()
                    expect(page.locator('#range-end')).to_have_value('2025-03-01')
                    assert page.url==saved_url

                    # Unknown valid address is an explicit empty result, not all trades.
                    page.locator('#wallet-ids').fill('0x'+'f'*40)
                    page.locator('#apply-filters').click()
                    expect(page.locator('#metric-fills')).to_have_text('0')
                    expect(page.locator('#chart-message')).to_contain_text('No matching wallet fills')
                    expect(page.locator('#chart-loading')).to_be_hidden()
                    page.locator('#clear-wallets').click()
                    expect(page.locator('#filter-badge')).to_have_text('ALL WALLETS')
                    expect(page.locator('#chart-loading')).to_be_hidden()
                    expect(page.locator('#metric-fills')).to_have_text(str(day['stats']['fill_count']))
                    page.locator('#all-dates').click()
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
                    # Mobile controls remain usable, not just overflow-free.
                    page.locator('#range-start').fill('2025-03-01')
                    page.locator('#range-end').fill('2025-03-02')
                    page.locator('#apply-filters').click()
                    expect(page.locator('#chart-loading')).to_be_hidden()
                    expect(page.locator('#range-end')).to_have_value('2025-03-02')
                    assert not errors,errors
                    print('Browser smoke passed: real ingestion/API, inline UTC dates, wallet roles and multi-address filtering, CSV consistency, permalink reload, no-match and invalid-input states, inspector, event view, dark mode and mobile layout.')
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
