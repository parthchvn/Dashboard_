from pathlib import Path
import subprocess

p=Path('tests/browser_smoke.py')
s=p.read_text()
marker='                    # Invalid dates do not silently change the active view.'
block='''                    # Granularity lives in the filter form and is applied with dates.
                    expect(page.locator('#filter-form #level')).to_have_count(1)
                    expect(page.locator('#level')).to_be_visible()
                    for chosen in ('1m','2m'):
                        page.locator('#level').select_option(chosen)
                        # A draft selection does not change the active view before Apply.
                        if chosen=='1m': expect(page.locator('#resolution-badge')).to_contain_text('RAW')
                        with page.expect_response(lambda r:'/series?' in r.url and ('level='+chosen) in r.url) as bars_response:
                            page.locator('#apply-filters').click()
                        bars=bars_response.value.json()
                        expect(page.locator('#chart-loading')).to_be_hidden()
                        assert bars['level']==chosen
                        assert sum(b['fill_count'] for b in bars['rows'])==day['stats']['fill_count']
                        assert bars['stats']==day['stats']
                    expect(page.locator('#resolution-badge')).to_contain_text('2M BARS')
                    page.screenshot(path=str(output/'granularity-desktop.png'),full_page=True)
                    page.locator('#level').select_option('custom')
                    expect(page.locator('#custom-granularity')).to_be_visible()
                    page.locator('#granularity-minutes').fill('7')
                    with page.expect_response(lambda r:'/series?' in r.url and 'level=7m' in r.url) as custom_response:
                        page.locator('#apply-filters').click()
                    custom=custom_response.value.json()
                    assert custom['bin_seconds']==420 and custom['stats']==day['stats']
                    expect(page.locator('#chart-loading')).to_be_hidden()
                    custom_url=page.url
                    assert 'level=7m' in custom_url
                    page.reload()
                    expect(page.locator('#resolution-badge')).to_contain_text('7M BARS')
                    expect(page.locator('#chart-loading')).to_be_hidden()
                    expect(page.locator('#level')).to_have_value('custom')
                    expect(page.locator('#granularity-minutes')).to_have_value('7')
                    assert page.url==custom_url
                    # Missing custom overlays must not break a valid custom chart.
                    page.locator('#show-reviews').check()
                    expect(page.locator('#review-list')).to_contain_text('No 7m overlay')
                    page.locator('#show-reviews').uncheck()
                    for bad in ('0','1.5','1441'):
                        page.locator('#granularity-minutes').fill(bad)
                        page.locator('#apply-filters').click()
                        expect(page.locator('#filter-error')).to_contain_text('whole number')
                        expect(page.locator('#resolution-badge')).to_contain_text('7M BARS')
                        assert page.url==custom_url
                    page.locator('#level').select_option('auto')
                    page.locator('#apply-filters').click()
                    expect(page.locator('#resolution-badge')).to_contain_text('RAW')
                    expect(page.locator('#chart-loading')).to_be_hidden()
'''
assert marker in s
s=s.replace(marker,block+marker)
marker='                    # Copied views retain wallet and UTC date filters after a reload.'
block='''                    # Two-minute bars and raw CSV remain consistent under wallet OR.
                    page.locator('#level').select_option('2m')
                    with page.expect_response(lambda r:'/series?' in r.url and 'level=2m' in r.url) as wallet_bars_response:
                        page.locator('#apply-filters').click()
                    wallet_bars=wallet_bars_response.value.json()
                    expect(page.locator('#chart-loading')).to_be_hidden()
                    assert wallet_bars['stats']==combined['stats']
                    assert sum(b['fill_count'] for b in wallet_bars['rows'])==len(expected)
                    with page.expect_download() as raw_download:
                        page.locator('#export').click()
                    assert len(list(csv.DictReader(Path(raw_download.value.path()).read_text().splitlines())))==len(expected)
'''
assert marker in s
s=s.replace(marker,block+marker)
s=s.replace("                    assert page.url==saved_url\n", "                    assert page.url==saved_url\n                    expect(page.locator('#level')).to_have_value('2m')\n")
s=s.replace("                    page.locator('#all-dates').click()", "                    page.locator('#level').select_option('auto')\n                    page.locator('#apply-filters').click()\n                    expect(page.locator('#chart-loading')).to_be_hidden()\n                    page.locator('#all-dates').click()")
s=s.replace("                    page.locator('#range-end').fill('2025-03-02')\n                    page.locator('#apply-filters').click()", "                    page.locator('#range-end').fill('2025-03-02')\n                    page.locator('#level').select_option('custom')\n                    page.locator('#granularity-minutes').fill('3')\n                    page.locator('#apply-filters').click()")
s=s.replace("                    expect(page.locator('#range-end')).to_have_value('2025-03-02')", "                    expect(page.locator('#range-end')).to_have_value('2025-03-02')\n                    expect(page.locator('#resolution-badge')).to_contain_text('3M BARS')\n                    assert page.locator('html').evaluate('(el) => el.scrollWidth')<=390\n                    page.screenshot(path=str(output/'granularity-mobile.png'),full_page=True)")
p.write_text(s)

p=Path('tests/filters.test.cjs')
s=p.read_text()+'''const {normalizeLevel,granularity,granularityControls}=require('../xvi/static/filters.js');
for(const level of ['auto','raw','30s','1m','2m','5m','10m','15m','30m','1h','4h','1d','7m']) {
  assert.equal(normalizeLevel(level),level);
  const controls=granularityControls(level);
  assert.equal(granularity(controls.choice,controls.minutes),level);
}
for(const [value,expected] of [['1','1m'],['2','2m'],['3','3m'],['7','7m'],['60','1h'],['240','4h'],['1440','1d']]) {
  assert.equal(granularity('custom',value),expected);
}
for(const invalid of ['', '0','-1','1.5','1441','1e2','01','NaN','2m']) assert.throws(()=>granularity('custom',invalid),/whole number/);
for(const invalid of ['__proto__','constructor','../../raw','2m;DROP','1441m','auto()']) assert.throws(()=>normalizeLevel(invalid),/granularity/);
assert.deepEqual(granularityControls('7m'),{choice:'custom',minutes:'7'});
console.log('Granularity semantics passed: presets, custom integer minutes, canonical aliases, restored controls, invalid values.');
'''
p.write_text(s)

p=Path('README.md')
s=p.read_text()
s=s.replace('**Aggregation is exact.** The levels are `raw`, `30s`, `1m`, `5m`, `1h`, and `1d`.', '**Aggregation is exact.** Cached presets are `raw`, `30s`, `1m`, `2m`, `5m`, `10m`, `15m`, `30m`, `1h`, `4h`, and `1d`. Custom whole-minute chart intervals from `1m` to `1440m` are also supported.')
marker='## Data semantics and safeguards'
block='''## Chart granularity

The **Filter this market** row now contains **Initial date**, **Final date**, **Granularity**, **Wallet IDs**, and **Wallet role**. Set all of them and click **Apply filters** once.

Choose **1 minute**, **2 minutes**, **5 minutes**, **10 minutes**, **15 minutes**, **30 minutes**, **1 hour**, **4 hours**, or **1 day**. **Raw fills**, **30 seconds**, and zoom-aware **Auto** are also available. **Custom minutes…** accepts any whole number from **1 to 1440**, such as 3 or 7. Invalid intervals do not change the active view. Manual granularity stays fixed when zooming or changing wallet filters; **Auto** chooses the displayed resolution. Copy view and reload retain the applied interval.

Granularity groups the **price chart and volume bars**, not the trade table or CSV export. A 2-minute bar summarizes all matching raw fills in that interval, with exact OHLC, share-weighted VWAP, weighted median, distinct transaction count, and notional. It does not discard every other point or average the 1-minute medians. Full-window statistics stay identical when only granularity changes. Wallet filtering happens **before** aggregation.

Bins are aligned to Unix epoch UTC and labeled by their end, with left-closed intervals. For example, ordinary 2-minute bins are `[12:00,12:02)`, `[12:02,12:04)`. A custom interval that does not divide one day (such as 7 minutes) stays on that fixed epoch grid across midnight; it is not restarted at the selected initial date. Boundary bins only include the selected-window fills. Empty bins remain absent.

The API accepts `level=2m`, `level=7m`, etc. Equivalent preset aliases such as `60m` canonicalize to `1h`. Custom intervals without a preset are aggregated from the selected raw window on demand, without building another full-history cache. Existing cached intervals and imported databases need no migration. Manual requests over 6,000 observed bars are rejected with a clear message; choose a shorter date range, a coarser interval, or Auto. They are never silently downsampled. Optional review overlays are used only at their exact level; a missing custom-level overlay is shown as unavailable, not substituted with another detector's interval.

'''
assert marker in s
s=s.replace(marker,block+marker)
p.write_text(s)
expected={
 'README.md':'ff0e1e03305edf6163aedd28fb1dfab90ebc35ed',
 'tests/browser_smoke.py':'604fda1d298044ca6187be37db2c1aba3cbfc56c',
 'tests/filters.test.cjs':'d1b295985026ac4c9a3007dc8724f38940e6a318',
 'tests/test_granularity.py':'af8e4c60d76980ff708e194578c5b5bd2164a68a'
}
for path,sha in expected.items():
    assert subprocess.check_output(['git','hash-object',path],text=True).strip()==sha,path
